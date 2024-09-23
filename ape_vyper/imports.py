import os
from collections.abc import Iterable, Iterator
from functools import cached_property
from pathlib import Path
from typing import Optional, Union

from ape.logging import LogLevel, logger
from ape.managers import ProjectManager
from ape.managers.project import Dependency
from ape.utils import ManagerAccessMixin, get_relative_path

from ape_vyper import FileType
from ape_vyper._utils import lookup_source_from_site_packages

BUILTIN_PREFIXES = ("vyper", "ethereum")

# Don't ever try to compile these on their own.
_KNOWN_PACKAGES_NOT_TO_COMPILE = ("snekmate",)


class Import:
    def __init__(
        self,
        project: ProjectManager,
        importer: Path,
        value: str,
        use_absolute_paths: Optional[bool] = None,
    ):
        self.project = project
        self.importer = importer
        self.initial_value: str = value
        self._is_relative: Optional[bool] = None  # Relative import
        self._use_absolute_paths: Optional[bool] = (
            use_absolute_paths  # Use absolute path source IDs
        )

    def __repr__(self) -> str:
        return f"<import {self.initial_value}"

    @cached_property
    def _pathified_value(self) -> str:
        value = self.initial_value.replace(".", os.path.sep).lstrip(os.path.sep)
        if dots := self.dots_prefix:
            return f"{dots}{os.path.sep}{value}"

        return value

    @cached_property
    def source_id(self) -> str:
        if self.use_absolute_paths:
            return str(self.path)

        elif data := self._local_data:
            return data["source_id"]

        elif site_pkg := self.site_package_info:
            return f"{site_pkg[0]}".split(f"site-packages{os.path.sep}")[-1]

        elif dependency_info := self.dependency_info:
            return f"{dependency_info[0]}"

        return self._pathified_value

    @property
    def is_local(self) -> bool:
        return bool(self._local_data)

    @property
    def sub_project(self) -> Optional[ProjectManager]:
        if self.is_builtin:
            return None
        elif self.is_local:
            return self.project
        elif self.site_package_info:
            return self.site_package_info[-1]
        elif self.dependency_info:
            return self.dependency_info[-1].project

        return None

    @cached_property
    def dependency_name(self) -> Optional[str]:
        # NOTE: May not be a dependency though.
        if self.is_relative:
            return None

        return self._pathified_value.split(os.path.sep)[0]

    @cached_property
    def dependency_filestem(self) -> Optional[str]:
        # NOTE: May not be a dependency though.
        return self._pathified_value.replace(f"{self.dependency_name}{os.path.sep}", "")

    @cached_property
    def site_package_info(self) -> Optional[tuple[Path, ProjectManager]]:
        if not (dependency_name := self.dependency_name):
            return None
        elif not (dependency_filestem := self.dependency_filestem):
            return None

        return lookup_source_from_site_packages(dependency_name, dependency_filestem)

    @cached_property
    def dependency_info(self) -> Optional[tuple[str, Dependency]]:
        dependency_name = self.dependency_name
        if dependency_name not in [x.name for x in self.project.dependencies]:
            return None

        for version_str, dep_project in self.project.dependencies[dependency_name].items():
            dependency = self.project.dependencies.get_dependency(dependency_name, version_str)
            contracts_path = dep_project.contracts_folder
            dependency_source_prefix = f"{get_relative_path(contracts_path, dep_project.path)}"
            source_id_stem = (
                f"{dependency_source_prefix}{os.path.sep}{self.dependency_filestem}".lstrip(
                    f"{os.path.sep}."
                )
            )
            for ext in (".vy", ".vyi", ".json"):
                source_id = f"{source_id_stem}{ext}"
                if source_id not in dep_project.sources:
                    continue

                return (source_id, dependency)

        return None

    @cached_property
    def is_site_package(self) -> bool:
        if path := self.path:
            return any(p.name == "site-packages" for p in path.parents)

        return False

    @cached_property
    def is_ape_dependency(self) -> bool:
        return self.dependency_info is not None

    @property
    def use_absolute_paths(self) -> Optional[bool]:
        if self._use_absolute_paths is not None:
            return self._use_absolute_paths

        # Unknown.
        return None

    @cached_property
    def path(self) -> Optional[Path]:
        if self.is_builtin:
            return None

        elif data := self._local_data:
            return data["path"]

        elif package_info := self.site_package_info:
            return package_info[0]

        elif dependency_info := self.dependency_info:
            source_id, dependency = dependency_info
            return dependency.project.path / source_id

        # Unknown.
        return None

    @cached_property
    def _local_data(self) -> dict:
        local_prefix_relative = self._local_relative_prefix
        local_prefix_absolute = self._local_absolute_prefix
        source_id = None
        if (self.project.path / f"{local_prefix_relative}{FileType.SOURCE}").is_file():
            # Relative source.
            source_id = f"{local_prefix_relative}{FileType.SOURCE.value}"
        elif (self.project.path / f"{local_prefix_relative}{FileType.INTERFACE}").is_file():
            # Relative interface.
            source_id = f"{local_prefix_relative}{FileType.INTERFACE.value}"
        elif (self.project.path / f"{local_prefix_relative}.json").is_file():
            # Relative JSON interface.
            source_id = f"{local_prefix_relative}.json"
        elif (self.project.path / f"{local_prefix_absolute}{FileType.SOURCE}").is_file():
            # Absolute source.
            source_id = f"{local_prefix_absolute}{FileType.SOURCE.value}"
        elif (self.project.path / f"{local_prefix_absolute}{FileType.INTERFACE}").is_file():
            # Absolute interface.
            source_id = f"{local_prefix_absolute}{FileType.INTERFACE.value}"
        elif (self.project.path / f"{local_prefix_absolute}.json").is_file():
            # Absolute JSON interface.
            source_id = f"{local_prefix_absolute}.json"

        if not source_id:
            # Not local.
            return {}

        path = self.project.path / source_id
        if "site-packages" in str(path) and not source_id.startswith(self.project.name):
            # Site-package dependencies must attach their name to the source ID.
            source_id = f"{self.project.name}{os.path.sep}{source_id}"

        return {"path": path, "source_id": source_id}

    @property
    def _relative_path_sin_ext(self) -> Optional[Path]:
        # NOTE: Cannot use `self.path` - must only use string value.
        if self.is_relative is False:
            return None

        # NOTE: Still calculate if self.relative is None
        return (self.importer.parent / self._pathified_value.lstrip(os.path.sep)).resolve()

    @property
    def _absolute_path_sin_ext(self) -> Optional[Path]:
        # NOTE: Cannot use `self.path` - must only use string value.
        if self._relative_path_sin_ext is True:
            return None

        # NOTE: Still calculate if self.relative is None
        return (self.project.path / self._pathified_value.lstrip(os.path.sep)).resolve()

    @cached_property
    def is_builtin(self) -> bool:
        return any(self._pathified_value.startswith(p) for p in BUILTIN_PREFIXES)

    @cached_property
    def dots_prefix(self) -> str:
        dots = ""
        value = str(self.initial_value)
        while value.startswith("."):
            dots += value[0]
            value = value[1:]

        return dots

    @property
    def is_relative(self) -> Optional[bool]:
        if self._is_relative is not None:
            return self._is_relative

        elif self.dots_prefix:
            # There is a dots-prefix. Definitely relative.
            self._is_relative = True
            return True

        # Still unknown.
        return None

    @property
    def _local_relative_prefix(self) -> Optional[str]:
        return (
            None
            if self._relative_path_sin_ext is None
            else str(self._relative_path_sin_ext)
            .replace(f"{self.project.path}", "")
            .lstrip(os.path.sep)
        )

    @property
    def _local_absolute_prefix(self) -> Optional[str]:
        return (
            None
            if self._absolute_path_sin_ext is None
            else str(self._absolute_path_sin_ext)
            .replace(f"{self.project.path}", "")
            .lstrip(os.path.sep)
        )


class ImportMap(dict[Path, list[Import]]):
    def __init__(self, project: ProjectManager, paths: list[Path]):
        self.project = project

        # Even though we build up mappings of all sources, as may be referenced
        # later on and that prevents re-calculating over again, we only
        # "show" the items requested.
        self._request_view: list[Path] = paths

    def __getitem__(self, item: Union[str, Path], *args, **kwargs) -> list[Import]:
        if isinstance(item, str) or not item.is_absolute():
            path = self.project.path / item
            return super().__getitem__(path, *args, **kwargs)
        else:
            return super().__getitem__(item, *args, **kwargs)

    def __setitem__(self, item: Union[str, Path], value: list[Import]):
        if isinstance(item, str) or not item.is_absolute():
            path = self.project.path / item
            super().__setitem__(path, value)
        else:
            super().__setitem__(item, value)

    def __contains__(self, item: Union[str, Path]) -> bool:  # type: ignore
        if isinstance(item, str) or not item.is_absolute():
            path = self.project.path / item
            return super().__contains__(path)
        else:
            return super().__contains__(item)

    def keys(self) -> list[Path]:  # type: ignore
        result = []
        for path in super().keys():
            if path not in self._request_view:
                continue

            result.append(path)

        return result

    def values(self) -> list[list[Import]]:  # type: ignore
        result = []
        for key in self.keys():
            result.append(self[key])

        return result

    def items(self) -> list[tuple[Path, list[Import]]]:  # type: ignore
        result = []
        for path, import_ls in super().items():
            if path not in self._request_view:
                continue

            result.append((path, import_ls))

        return result


class ImportResolver(ManagerAccessMixin):
    """
    Get and manage Vyper imports across projects and files.
    """

    # Map of project-ids to source-ids to import lists.
    _projects: dict[str, ImportMap] = {}
    _dependency_attempted_compile: set[str] = set()

    def get_imports(
        self,
        project: ProjectManager,
        contract_filepaths: Iterable[Path],
        use_absolute_paths: Optional[bool] = None,
    ) -> ImportMap:
        paths = list(contract_filepaths)
        reset_view = None
        if project.project_id not in self._projects:
            self._projects[project.project_id] = ImportMap(project, paths)
        else:
            # Change the items we "view". Some (or all) may need to be added as well.
            reset_view = self._projects[project.project_id]._request_view
            self._projects[project.project_id]._request_view = paths

        try:
            import_map = self._get_imports(paths, project, use_absolute_paths)
        finally:
            if reset_view is not None:
                self._projects[project.project_id]._request_view = reset_view

        return import_map

    def _get_imports(
        self, paths: list[Path], project: ProjectManager, use_absolute_paths: Optional[bool]
    ) -> ImportMap:
        if use_absolute_paths is None:
            # When compiling projects outside the cwd, we must
            # use absolute paths.
            use_absolute_paths = project.path != Path.cwd()

        import_map = self._projects[project.project_id]
        for path in paths:
            if path in import_map:
                # Already handled.
                continue

            elif not path.is_file():
                # Let it fail later, just in case.
                import_map[path] = []
                continue

            else:
                import_map[path] = []
                content = path.read_text(encoding="utf8").splitlines()
                for line in content:
                    for import_data in self._parse_imports_from_line(
                        line, path, project, use_absolute_paths
                    ):
                        import_map[path].append(import_data)

        return import_map

    def _parse_imports_from_line(
        self, line: str, path: Path, project: ProjectManager, use_absolute_paths: bool
    ) -> Iterator[Import]:
        if not (prefix := _parse_import_line(line)):
            return None

        import_data = Import(
            project=project,
            importer=path,
            value=prefix,
            use_absolute_paths=use_absolute_paths,
        )
        # Calculate path before yielding.
        import_path = import_data.path
        yield import_data

        if import_data.is_builtin:
            # For builtins, we are already done.
            return

        elif import_path is not None and import_path in self._projects[project.project_id]:
            # Yield already-known imports of import-path.
            if import_path in self._projects[project.project_id]:
                yield from self._projects[project.project_id][import_path]

        elif sub_project := import_data.sub_project:
            # Calculate imports of import_path for the first time.
            if dependency_info := import_data.dependency_info:
                _, dependency = dependency_info
                self._compile_dependency_if_needed(dependency)

            if import_path := import_path:
                # Imports from imports. Note: this call will cache them.
                sub_import_map = self.get_imports(
                    sub_project,
                    (import_path,),
                    use_absolute_paths=import_data.use_absolute_paths,
                )
                if import_path in sub_import_map:
                    yield from sub_import_map[import_path]

        elif dependency_name := import_data.dependency_name:
            logger.error(
                f"(project={project.project_id}). '{dependency_name}' may not be installed. "
                "Could not find it in Ape dependencies or Python's site-packages."
            )

        yield import_data

    def _compile_dependency_if_needed(self, dependency: Dependency):
        if (
            dependency.name in _KNOWN_PACKAGES_NOT_TO_COMPILE
            or dependency.project.manifest.contract_types
            or dependency.package_id in self._dependency_attempted_compile
        ):
            # Can' compile, or already compiled or attempted.
            return

        self._dependency_attempted_compile.add(dependency.package_id)
        # In this case, the dependency *must* be compiled
        # so the ABIs can be found later on.
        with logger.at_level(LogLevel.ERROR):
            try:
                dependency.compile()
            except Exception as err:
                # Compiling failed. Try to continue anyway to get
                # a better error from the Vyper compiler, in case
                # something else is wrong.
                logger.warning(
                    f"Failed to compile dependency '{dependency.name}' "
                    f"@ '{dependency.version}'.\n"
                    f"Reason: {err}"
                )


def _parse_import_line(line: str) -> Optional[str]:
    if line.startswith("import "):
        return line.replace("import ", "").split(" ")[0]
    elif line.startswith("from ") and " import " in line:
        import_line_parts = line.replace("from ", "").strip().split(" ")
        prefix = import_line_parts[0].strip()
        suffix = import_line_parts[2].strip()
        return f"{prefix}{suffix}" if prefix.endswith(".") else f"{prefix}.{suffix}"

    # Not an import line
    return None
