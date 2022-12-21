import ast
import csv
import hashlib
import json
import os
import shutil
import subprocess
from collections import OrderedDict
from enum import Enum
from pathlib import Path

import typer
from ruamel.yaml import YAML

from hub_utils.meltano_util import MeltanoUtil


class Kind(str, Enum):
    string = "string"
    boolean = "boolean"
    integer = "integer"
    object = "object"
    array = "array"

class Utilities:

    def __init__(self, auto_accept=False):
        self.yaml = YAML()
        self.auto_accept = auto_accept
        self.hub_root = os.getenv('HUB_ROOT_PATH', '/Users/pnadolny/Documents/Git/GitHub/meltano/hub')
        self.default_variants_path = f'{self.hub_root}/_data/default_variants.yml'
        self.maintainers_path = f'{self.hub_root}/_data/maintainers.yml'

    def _prompt(self, question, default_val=None, type=None):
        if self.auto_accept:
            return default_val
        if default_val:
            return typer.prompt(question, default=default_val, type=type)
        else:
            return typer.prompt(question, type=type)

    def _write_yaml(self, path, content):
        with open(path, "w") as f:
            self.yaml.dump(content, f)

    def _read_yaml(self, path):
        with open(path, "r") as f:
            data = self.yaml.load(f)
        return data

    @staticmethod
    def _get_plugin_name(repo_url: str):
        return repo_url.split("/")[-1]

    @staticmethod
    def _get_plugin_variant(repo_url: str):
        return repo_url.split("/")[-2].lower()

    @staticmethod
    def get_plugin_type(plugin_name: str):
        if 'tap-' in plugin_name and 'target-' in plugin_name:
            raise Exception(f'Type Unknown: {plugin_name}')
        if 'tap-' in plugin_name:
            return 'extractors'
        if 'target-' in plugin_name:
            return 'loaders'

    @staticmethod
    def _boilerplate_capabilities(plugin_type):
        if plugin_type == 'extractors':
            return [
                "catalog",
                "discover",
                "state"
            ]
        if plugin_type == 'loaders':
            return []

    @staticmethod
    def _scrape_keywords(meltano_sdk):
        if meltano_sdk:
            return f"['meltano_sdk']"
        return "[]"

    @staticmethod
    def _get_label(plugin_name, plugin_type=None):
        # TODO: not sure if this is the best place to do this
        name = plugin_name
        if plugin_type:
            if plugin_type == 'extractors':
                name = ''.join(plugin_name.split('tap-')[1:])
            elif plugin_type == 'loaders':
                name = ''.join(plugin_name.split('target-')[1:])
        return name.replace('_', ' ').replace('-', ' ').title()

    @staticmethod
    def _get_maintenance_status():
        return "active"

    def _build_settings(self, setting_list):
        settings = []
        settings_group_validation = []
        for setting in setting_list:
            label = self._prompt(f"[{setting}] `label`", default_val=self._get_label(setting))
            kind = self._prompt(f"[{setting}] `kind`", default_val=MeltanoUtil._parse_kind("string", {'name': setting}))
            description = self._prompt(f"[{setting}] `description`", default_val=MeltanoUtil._default_description(setting))
            required = self._prompt(f"[{setting}] `required`", default_val=False, type=bool)
            setting_details = {
                'name': setting,
                'label': label,
                'description': description
            }
            if kind != 'string':
                setting_details['kind'] = kind
            settings.append(setting_details)
            if required:
                settings_group_validation.append(setting)
        return settings, [settings_group_validation]

    def _compile_settings(self, seed=[]):
        settings = seed
        continue_prompts = True
        prompt_text = 'Lets collect all the settings, provide one at a time'
        while continue_prompts and not self.auto_accept:
            setting = self._prompt(f"{prompt_text}", default_val=settings)
            if str(setting) == str(settings):
                break
            settings.append(setting)
            prompt_text = 'Return an empty to end'
        return list(set(settings))

    @staticmethod
    def _string_to_literal(value):
        try:
            return ast.literal_eval(value)
        except:
            return value

    def _boilerplate_definition(
        self,
        repo_url,
        plugin_type,
        settings,
        settings_group_validation,
        name,
        namespace,
        pip_url,
        keywords,
        capabilities,
        executable,
        variant
    ):
        label = self._get_label(name, plugin_type=plugin_type)
        logo_name = label.lower().replace(' ', '-')
        plugin_def = {
            "name": name,
            "variant": variant,
            "label": self._prompt("label", label),
            "logo_url": f'/assets/logos/{plugin_type}/{logo_name}.png',
            "capabilities": capabilities,
            "description": self._prompt("description", ""),
            "domain_url": self._prompt("domain_url", ""),
            "keywords": keywords,
            "maintenance_status": self._prompt("maintenance_status", self._get_maintenance_status()),
            "namespace": namespace,
            "next_steps": "",
            "pip_url": pip_url,
            "repo": repo_url,
            "settings": settings,
            "settings_group_validation": settings_group_validation,
            "settings_preamble": "",
            "usage": "",
        }
        if executable:
            plugin_def["executable"] = executable
        return plugin_def

    def _write_definition(self, definition, plugin_type):
        dir_name = os.path.join(
            self.hub_root,
            '_data',
            'meltano',
            plugin_type,
            definition['name']
        )
        variant = definition['variant']
        Path(dir_name).mkdir(parents=True, exist_ok=True)
        yaml_path = Path(os.path.join(dir_name, f'{variant}.yml'))
        if not yaml_path.exists():
            self._write_yaml(
                os.path.join(dir_name, f'{variant}.yml'),
                definition
            )
            print(f'Definition: Updated')
        else:
            # TODO: show a diff or print
            overwrite = self._prompt(
                "Plugin definition already exists, overwrite it?",
                default_val=False,
                type=bool
            )
            if overwrite:
                self._write_yaml(
                    os.path.join(dir_name, f'{variant}.yml'),
                    definition
                )
            else:
                print(f'Definition: Skipping')
        return str(yaml_path)

    def _update_variant_file(self, plugin_type_defaults, plugin_name, plugin_variant, defaults, plugin_type):
        plugin_type_defaults[plugin_name] = plugin_variant
        defaults[plugin_type] = plugin_type_defaults
        self._write_yaml(self.default_variants_path, defaults)
        print(f'Default: Updated')

    def _handle_default_variant(self, plugin_name, plugin_variant, plugin_type):
        defaults = self._read_yaml(self.default_variants_path)
        plugin_type_defaults = defaults[plugin_type]
        if plugin_name not in plugin_type_defaults:
            self._update_variant_file(plugin_type_defaults, plugin_name, plugin_variant, defaults, plugin_type)
        else:
            current_default = plugin_type_defaults[plugin_name]
            overwrite = self._prompt(
                f"Default variant already exists [{current_default}], overwrite it?",
                default_val=False,
                type=bool
            )
            if overwrite:
                self._update_variant_file(plugin_type_defaults, plugin_name, plugin_variant, defaults, plugin_type)
            return True

    def _handle_maintainer(self, plugin_variant, repo_url):
        updated_maintainers = self._read_yaml(self.maintainers_path)
        if plugin_variant not in updated_maintainers:
            maintainer_name = plugin_variant
            updated_maintainers[plugin_variant] = {
                "label": maintainer_name,
                "url": "/".join(repo_url.split("/")[:-1]),
                "name": maintainer_name
            }
            print(f'Maintainer: Updated')
            updated_maintainers = dict(OrderedDict(sorted(updated_maintainers.items())))
            self._write_yaml(self.maintainers_path, updated_maintainers)
        else:
            print(f'Maintainer: Skipping')

    def _handle_logo(self, definition, plugin_type, variant_exists):
        if variant_exists and self._prompt(
            f"Use current variant's logo?",
            default_val=True,
            type=bool
        ):
            return

        image_path = self._prompt(
            "Path to image [.png] file, leave blank to skip",
            "skip"
        )
        # TODO: kind of a hack, not sure how to accept an empty string to skip properly
        if image_path == "skip":
            logo_file_name = definition['logo_url'].split('/')[-1]
            print('Logo: Placeholder Used')
        else:
            logo_file_name = definition['logo_url'].split('/')[-1]
            shutil.copyfile(image_path, f'{self.hub_root}/static/assets/logos/{plugin_type}/{logo_file_name}')

    def _reformat(self, plugin_type, plugin_name, variant):
        for file_path in [
            '_data/default_variants.yml',
            '_data/maintainers.yml',
            f'_data/meltano/{plugin_type}/{plugin_name}/{variant}.yml'
        ]:
            print(subprocess.run(
                f"poetry run python {self.hub_root}/utility_scripts/plugin_definitions/yaml_lint_fix.py {self.hub_root}/{file_path}".split(" "),
                cwd=self.hub_root,
                stdout=subprocess.PIPE,
                universal_newlines=True,
                check=True,
            ))

    @staticmethod
    def _install_test(plugin_name, plugin_type, pip_url, namespace, executable):
        MeltanoUtil.add(plugin_name, namespace, executable, pip_url, plugin_type)
        MeltanoUtil.help_test(plugin_name)

    def add(self, repo_url: str = None, definition_seed: dict = None):
        if not repo_url:
            repo_url = self._prompt("repo_url")
        plugin_name = self._prompt("plugin name", self._get_plugin_name(repo_url))
        plugin_type = self._prompt("plugin type", self.get_plugin_type(repo_url))
        pip_url = self._prompt("pip_url", f"git+{repo_url}.git")
        namespace = self._prompt("namespace", plugin_name.replace('-', '_'))
        executable = self._prompt("executable", plugin_name)
        is_meltano_sdk = self._prompt("is_meltano_sdk", True, type=bool)
        sdk_about_dict = None
        sdk_about_dict = self._test(
            plugin_name,
            plugin_type,
            pip_url,
            namespace,
            executable,
            is_meltano_sdk
        )
        if sdk_about_dict:
            settings, settings_group_validation, capabilities = MeltanoUtil._parse_sdk_about_settings(sdk_about_dict, enforce_desc=True)
        else:
            setting_list = self._compile_settings()
            settings, settings_group_validation = self._build_settings(setting_list)
            capabilities = self._string_to_literal(self._prompt("capabilities", self._boilerplate_capabilities(plugin_type)))
        keywords = self._string_to_literal(self._prompt("keywords", self._scrape_keywords(is_meltano_sdk)))
        definition = self._boilerplate_definition(
            repo_url,
            plugin_type,
            settings,
            settings_group_validation,
            plugin_name,
            namespace,
            pip_url,
            keywords,
            capabilities,
            executable,
            self._prompt("plugin variant", self._get_plugin_variant(repo_url)),
        )
        definition_path = self._write_definition(definition, plugin_type)
        variant = definition['variant']
        variant_exists = self._handle_default_variant(plugin_name, definition['variant'], plugin_type)
        self._handle_maintainer(variant, repo_url)
        self._handle_logo(definition, plugin_type, variant_exists)
        self._reformat(plugin_type, plugin_name, variant)
        print(definition_path)
        print(f'Adds {plugin_type} {plugin_name} ({variant})\n\n')

    def add_airbyte(self, definition_seed: dict = None):
        repo_url = 'https://github.com/z3z1ma/tap-airbyte'
        plugin_name = self._prompt("plugin name", 'tap-<source/x>')
        plugin_type = 'extractors'
        pip_url = f"git+{repo_url}.git"
        namespace = 'tap_airbyte'
        executable = 'tap-airbyte'
        variant = 'airbyte'
        sdk_about_dict = None
        sdk_about_dict = self._test_airbyte(
            plugin_name,
            plugin_type,
            pip_url,
            namespace,
            executable,
        )
        if sdk_about_dict:
            settings, settings_group_validation, capabilities = MeltanoUtil._parse_sdk_about_settings(sdk_about_dict, enforce_desc=True)
        else:
            setting_list = self._compile_settings()
            settings, settings_group_validation = self._build_settings(setting_list)
            capabilities = self._string_to_literal(self._prompt("capabilities", self._boilerplate_capabilities(plugin_type)))
        keywords = self._string_to_literal(self._prompt("keywords", self._scrape_keywords(True)))
        definition = self._boilerplate_definition(
            repo_url,
            plugin_type,
            settings,
            settings_group_validation,
            plugin_name,
            namespace,
            pip_url,
            keywords,
            capabilities,
            executable,
            variant,
        )
        definition_path = self._write_definition(definition, plugin_type)
        definition['variant'] = variant
        variant_exists = self._handle_default_variant(plugin_name, variant, plugin_type)
        self._handle_maintainer(variant, repo_url)
        self._handle_logo(definition, plugin_type, variant_exists)
        self._reformat(plugin_type, plugin_name, variant)
        print(definition_path)
        print(f'Adds {plugin_type} {plugin_name} ({variant})\n\n')

    def delete_rows(self, repo_urls_to_delete, edit_path, csv_path):
        with open(csv_path, 'r') as inp, open(edit_path, 'w') as out:
            writer = csv.writer(out)
            for row in csv.reader(inp):
                if row[0] in repo_urls_to_delete:
                    continue
                writer.writerow(row)

    def add_bulk(self, csv_path: str):
        edit_path = csv_path.split('.csv')[0] + '_edit.csv'
        csv_list = []
        repo_urls_to_delete = []
        with open(csv_path, 'r') as inp:
            csv_list = [row for row in csv.reader(inp)]
        for index, row in enumerate(csv_list):
            if index == 0:
                print(f"Skipping header {row}")
                continue
            repo_url = row[0]
            plugin_definition = json.loads(row[5])
            name_hash = hashlib.sha256(self._get_plugin_name(repo_url).encode()).hexdigest()
            do_add = self._prompt(f'Add {repo_url} - {name_hash}?', default_val=True, type=bool)
            if do_add:
                self.add(repo_url, definition_seed=plugin_definition)
                self._prompt('Pausing to commit changes...hit any key to continue')
            repo_urls_to_delete.append(repo_url)
            self.delete_rows(repo_urls_to_delete, edit_path, csv_path)

    def _retrieve_def(self, plugin_name, plugin_variant, plugin_type):
        def_path = f'{self.hub_root}/_data/meltano/{plugin_type}/{plugin_name}/{plugin_variant}.yml'
        return self._read_yaml(def_path)

    def _write_updated_def(self, plugin_name, plugin_variant, plugin_type, definition):
        def_path = f'{self.hub_root}/_data/meltano/{plugin_type}/{plugin_name}/{plugin_variant}.yml'
        self._write_yaml(
            def_path,
            definition
        )

    def _iterate_existing_settings(self, plugin_name, plugin_variant, plugin_type):
        def_path = f'{self.hub_root}/_data/meltano/{plugin_type}/{plugin_name}/{plugin_variant}.yml'
        return self._read_yaml(def_path)

    def _merge_definitions(
        self,
        existing_def,
        settings,
        keywords,
        m_status,
        caps,
        sgv
    ):
        new_def = existing_def.copy()
        new_def['settings'] = settings
        new_def['keywords'] = keywords
        new_def['maintenance_status'] = m_status
        new_def['capabilities'] = caps
        new_def['settings_group_validation'] = sgv
        return new_def

    def _test(self, plugin_name, plugin_type, pip_url, namespace, executable, is_meltano_sdk):
        try:
            if self._prompt("Run install test?", True, type=bool):
                self._install_test(plugin_name, plugin_type, pip_url, namespace, executable)
            if is_meltano_sdk:
                if self._prompt("Scrape SDK --about settings?", True, type=bool):
                    try:
                        return MeltanoUtil.sdk_about(plugin_name)
                    except Exception as e:
                        if self._prompt("Scrape failed! Provide as json?", True, type=bool):
                            return json.loads(self._prompt("Provide --about output"))
        except Exception as e:
            print(e)
        finally:
            MeltanoUtil.remove(plugin_name, plugin_type)

    def _test_airbyte(self, plugin_name, plugin_type, pip_url, namespace, executable):
        try:
            airbyte_name = self._prompt("airbyte_name (e.g. source-s3)")
            MeltanoUtil.add(plugin_name, namespace, executable, pip_url, plugin_type)
            MeltanoUtil.command(f'meltano config {plugin_name} set airbyte_spec.image airbyte/{airbyte_name}')
            MeltanoUtil.command(f'meltano config {plugin_name} set airbyte_spec.tag latest')
            MeltanoUtil.help_test(plugin_name)
            try:
                about_content = subprocess.run(
                    f"poetry run meltano invoke {plugin_name} --about --format=json".split(" "),
                    cwd=str(MeltanoUtil.get_cwd()) + '/test_meltano_project/',
                    stdout=subprocess.PIPE,
                    universal_newlines=True,
                    check=True,
                )
                about_json = about_content.stdout.split('Setup Instructions:')[0]
                print(about_json)
                return json.loads(about_json)
            except Exception as e:
                if self._prompt("Scrape failed! Provide as json?", True, type=bool):
                    return json.loads(self._prompt("Provide --about output"))
        except Exception as e:
            print(e)
        finally:
            MeltanoUtil.remove(plugin_name, plugin_type)

    def _update_base(self, repo_url, is_meltano_sdk=False):
        if not repo_url:
            repo_url = self._prompt("repo_url")
        plugin_name = self._prompt("plugin name", self._get_plugin_name(repo_url))
        plugin_type = self._prompt("plugin type", self.get_plugin_type(repo_url))
        plugin_variant = self._prompt("plugin variant", self._get_plugin_variant(repo_url))
        existing_def = self._retrieve_def(plugin_name, plugin_variant, plugin_type)
        sdk_def = self._test(
            plugin_name,
            plugin_type,
            existing_def['pip_url'],
            existing_def['namespace'],
            existing_def.get('executable', plugin_name),
            is_meltano_sdk
        )
        return repo_url, plugin_name, plugin_type, plugin_variant, existing_def, sdk_def

    def update(self, repo_url: str = None, definition_seed: dict = None):
        repo_url, plugin_name, plugin_type, plugin_variant, existing_def, sdk_def = self._update_base(repo_url)
        setting_names = [setting.get('name') for setting in existing_def.get('settings', [])]
        caps = self._string_to_literal(self._prompt("capabilities", existing_def.get('capabilities')))
        m_status = self._prompt("maintenance_status", existing_def.get('maintenance_status'))
        keywords = self._string_to_literal(self._prompt("keywords", existing_def.get('keywords')))
        settings, sgv = self._build_settings(self._compile_settings(setting_names))
        new_def = self._merge_definitions(
            existing_def,
            settings,
            keywords,
            m_status,
            caps,
            sgv,
        )
        self._write_updated_def(plugin_name, plugin_variant, plugin_type, new_def)
        self._reformat(plugin_type, plugin_name, plugin_variant)
        print(f'\nUpdates {plugin_type} {plugin_name} ({plugin_variant})\n\n')

    def update_sdk(self, repo_url: str = None, definition_seed: dict = None):
        repo_url, plugin_name, plugin_type, plugin_variant, existing_def, sdk_def = self._update_base(repo_url, is_meltano_sdk=True)
        settings, settings_group_validation, capabilities = MeltanoUtil._parse_sdk_about_settings(sdk_def)
        new_def = self._merge_definitions(
            existing_def,
            settings,
            self._string_to_literal(self._prompt("keywords", self._scrape_keywords(True))),
            self._prompt("maintenance_status", self._get_maintenance_status()),
            capabilities,
            settings_group_validation,
        )
        self._write_updated_def(plugin_name, plugin_variant, plugin_type, new_def)
        print(f'\nUpdates {plugin_type} {plugin_name} (SDK based - {plugin_variant})\n\n')

if __name__ == "__main__":
    util = Utilities(False)
    util.add_airbyte()
    # util.update("https://github.com/Yoast/singer-tap-postmark")
    # util.update_sdk("https://github.com/hotgluexyz/tap-procore")
    # util.add_bulk('/Users/pnadolny/Documents/Git/GitHub/pnadolny/hub-utils/other_scripts/export_edit.csv')