from typing import Optional

from ape.api import PluginConfig
from ape.utils import pragma_str_to_specifier_set
from packaging.specifiers import SpecifierSet
from pydantic import field_serializer, field_validator, model_validator


class VyperConfig(PluginConfig):
    version: Optional[SpecifierSet] = None
    """
    Configure a version to use for all files,
    regardless of pragma.
    """

    evm_version: Optional[str] = None
    """
    The evm-version or hard-fork name.
    """

    import_remapping: list["Remapping"] = []
    """
    Configuration of an import name mapped to a dependency listing.
    To use a specific version of a dependency, specify using ``@`` symbol.

    Usage example::

        vyper:
          import_remapping:
            - "dep_a=dependency_a@0.1.1"
            - "dep_b=dependency"  # Uses only version. Will raise if more than 1.

    """

    enable_decimals: Optional[bool] = None
    """
    On Vyper 0.4, to use decimal types, you must enable it.
    Defaults to ``None`` to avoid misleading that ``False``
    means you cannot use decimals on a lower version.
    """

    @field_validator("version", mode="before")
    def validate_version(cls, value):
        return pragma_str_to_specifier_set(value) if isinstance(value, str) else value

    @field_serializer("version")
    def serialize_version(self, value: Optional[SpecifierSet], _info) -> Optional[str]:
        if version := value:
            return str(version)

        return None


class Remapping(PluginConfig):
    key: str
    dependency_name: str
    dependency_version: Optional[None] = None

    @model_validator(mode="before")
    @classmethod
    def validate_str(cls, value):
        if isinstance(value, str):
            parts = value.split("=")
            key = parts[0].strip()
            value = parts[1].strip()
            if "@" in value:
                value_parts = value.split("@")
                dep_name = value_parts[0].strip()
                dep_version = value_parts[1].strip()
            else:
                dep_name = value
                dep_version = None

            return {"key": key, "dependency_name": dep_name, "dependency_version": dep_version}

        return value

    def __str__(self) -> str:
        value = self.dependency_name
        if _version := self.dependency_version:
            value = f"{value}@{_version}"

        return f"{self.key}={value}"
