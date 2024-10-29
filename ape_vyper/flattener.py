from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ape.logging import logger
from ape.utils import ManagerAccessMixin, get_relative_path
from ethpm_types.source import Content

from ape_vyper._utils import get_version_pragma_spec
from ape_vyper.ast import source_to_abi
from ape_vyper.interface import (
    extract_import_aliases,
    extract_imports,
    extract_meta,
    generate_interface,
)

if TYPE_CHECKING:
    from ape.managers.project import ProjectManager

    from ape_vyper.compiler import VyperCompiler


class Flattener(ManagerAccessMixin):
    @property
    def vyper(self) -> "VyperCompiler":
        return self.compiler_manager.vyper

    def flatten(
        self,
        path: Path,
        project: Optional["ProjectManager"] = None,
    ) -> Content:
        """
        Returns the flattened contract suitable for compilation or verification as a single file
        """
        pm = project or self.local_project
        src = self._flatten_source(path, project=pm)
        return Content({i: ln for i, ln in enumerate(src.splitlines())})

    def _flatten_source(
        self,
        path: Path,
        project: Optional["ProjectManager"] = None,
        include_pragma: bool = True,
        sources_handled: Optional[set[Path]] = None,
        warn_flattening_modules: bool = True,
    ) -> str:
        pm = project or self.local_project
        handled = sources_handled or set()
        handled.add(path)
        imports = {
            imp.path: imp
            for imp in self.vyper._import_resolver.get_imports(pm, (path,)).get(path, [])
            if not imp.is_builtin and imp.path
        }

        interfaces_source = ""
        og_source = (pm.path / path).read_text(encoding="utf8")

        # Get info about imports and source meta
        aliases = extract_import_aliases(og_source)
        pragma, source_without_meta = extract_meta(og_source)
        version_specifier = get_version_pragma_spec(pragma) if pragma else None
        stdlib_imports, _, source_without_imports = extract_imports(source_without_meta)
        flattened_modules = ""
        modules_prefixes: set[str] = set()

        # Source by source ID for greater consistency..
        for import_path in sorted(
            imports, key=lambda p: f"{get_relative_path(p.absolute(), pm.path)}"
        ):
            import_info = imports[import_path]

            # Vyper imported interface names come from their file names
            file_name = import_path.stem
            # If we have a known alias, ("import X as Y"), use the alias as interface name
            import_name = aliases[file_name] if file_name in aliases else file_name
            dependency = import_info.sub_project
            if (
                dependency is not None
                and dependency.project_id != pm.project_id
                and dependency.manifest.contract_types
            ):
                abis = [
                    el
                    for k in dependency.manifest.contract_types.keys()
                    for el in dependency.manifest.contract_types[k].abi
                ]
                interfaces_source += generate_interface(abis, import_name)
                continue

            # Generate an ABI from the source code
            elif import_path.is_file():
                if (
                    version_specifier
                    and version_specifier.contains("0.4.0")
                    and import_path.suffix != ".vyi"
                ):
                    if warn_flattening_modules:
                        logger.warning(
                            "Flattening modules DOES NOT yield the same bytecode! "
                            "This is **NOT** valid for contract-verification."
                        )
                        warn_flattening_modules = False

                    modules_prefixes.add(import_path.stem)
                    if import_path in handled:
                        # We have already included this source somewhere.
                        continue

                    # Is a module or an interface imported from a module.
                    # Copy in the source code directly.
                    flattened_module = self._flatten_source(
                        import_path,
                        project=pm,
                        include_pragma=False,
                        sources_handled=handled,
                        warn_flattening_modules=warn_flattening_modules,
                    )
                    flattened_modules = f"{flattened_modules}\n\n{flattened_module}"

                else:
                    # Vyper <0.4 interface from folder other than interfaces/
                    # such as a .vyi file in the contracts folder.
                    abis = source_to_abi(import_path.read_text(encoding="utf8"))
                    interfaces_source += generate_interface(abis, import_name)

        def no_nones(it: Iterable[Optional[str]]) -> Iterable[str]:
            # Type guard like generator to remove Nones and make mypy happy
            for el in it:
                if el is not None:
                    yield el

        pragma_to_include = pragma if include_pragma else ""

        # Join all the OG and generated parts back together
        flattened_source = "\n\n".join(
            no_nones(
                (
                    pragma_to_include,
                    stdlib_imports,
                    interfaces_source,
                    flattened_modules,
                    source_without_imports,
                )
            )
        )

        # Clear module-usage prefixes.
        for prefix in modules_prefixes:
            # Replace usage lines like 'zero_four_module.moduleMethod()'
            # with 'self.moduleMethod()'.
            flattened_source = flattened_source.replace(f"{prefix}.", "self.")

        # Remove module-level doc-strings, as it causes compilation issues
        # when used in root contracts.
        lines_no_doc: list[str] = []
        in_str_comment = False
        for line in flattened_source.splitlines():
            line_stripped = line.rstrip()
            if not in_str_comment and line_stripped.startswith('"""'):
                if line_stripped == '"""' or not line_stripped.endswith('"""'):
                    in_str_comment = True
                continue

            elif in_str_comment:
                if line_stripped.endswith('"""'):
                    in_str_comment = False

                continue

            lines_no_doc.append(line)

        flattened_source = "\n".join(lines_no_doc)

        # TODO: Replace this nonsense with a real code formatter
        def format_source(source: str) -> str:
            while "\n\n\n\n" in source:
                source = source.replace("\n\n\n\n", "\n\n\n")
            return source

        return format_source(flattened_source)
