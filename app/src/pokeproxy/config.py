from __future__ import annotations

import base64
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version
from typing import Literal

from google.protobuf.message import DecodeError
from pydantic import BaseModel, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pokeproxy.proto import pokemon_pb2

# RFC 2104 recommends a key at least as long as the hash output (32 bytes for
# SHA-256). We enforce 16 (128 bits) instead: still far beyond brute-force reach
# for an HMAC key, and it keeps this check to one concern — refusing keys that
# are absent, malformed, or trivially weak. Raising it later is a one-line
# change plus a secret rotation.
MIN_HMAC_KEY_BYTES = 16

_HMAC_KEY_HELP = (
    "Set POKEPROXY_HMAC_KEY to a base64-encoded key of at least "
    f"{MIN_HMAC_KEY_BYTES} bytes. Generate one with: openssl rand -base64 32"
)


def _package_build_version() -> str:
    """Read the installed package version for pokeproxy_build_info.

    Falls back rather than raising: a missing dist-info (e.g. a stray local
    invocation outside the built venv) shouldn't block startup over a label
    value.
    """
    try:
        return _package_version("pokeproxy")
    except PackageNotFoundError:
        return "unknown"


def _decode_hmac_key(raw: str) -> bytes:
    """Decode the configured HMAC key, rejecting anything unusable.

    Decoding is strict so a mistyped or whitespace-mangled value is an error
    rather than being silently truncated into a shorter key than intended.
    """
    try:
        key = base64.b64decode(raw, validate=True)
    except ValueError as e:
        raise ValueError(
            f"POKEPROXY_HMAC_KEY is not valid base64: {e}. {_HMAC_KEY_HELP}"
        ) from e

    if len(key) < MIN_HMAC_KEY_BYTES:
        raise ValueError(
            f"POKEPROXY_HMAC_KEY decodes to {len(key)} byte(s), below the "
            f"{MIN_HMAC_KEY_BYTES}-byte minimum. {_HMAC_KEY_HELP}"
        )

    return key


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=False, extra="ignore"
    )

    pokeproxy_hmac_key: str
    pokeproxy_config: str
    pokeproxy_port: int = 8000
    redis_url: str = "redis://localhost:6379/0"
    log_level: str = "INFO"
    forward_max_attempts: int = 3
    forward_deadline_seconds: float = 10.0
    forward_attempt_timeout_seconds: float = 3.0
    redis_connect_timeout_seconds: float = 2.0
    redis_socket_timeout_seconds: float = 2.0
    cache_ttl_seconds: int = 300
    pokeproxy_revision: str = "unknown"

    @field_validator("pokeproxy_hmac_key")
    @classmethod
    def _check_hmac_key(cls, raw: str) -> str:
        _decode_hmac_key(raw)
        return raw

    @field_validator("forward_max_attempts", "cache_ttl_seconds")
    @classmethod
    def _check_at_least_one(cls, value: int, info: ValidationInfo) -> int:
        if value < 1:
            raise ValueError(f"{info.field_name.upper()} must be at least 1")
        return value

    @field_validator(
        "forward_deadline_seconds",
        "forward_attempt_timeout_seconds",
        "redis_connect_timeout_seconds",
        "redis_socket_timeout_seconds",
    )
    @classmethod
    def _check_positive_seconds(cls, value: float, info: ValidationInfo) -> float:
        if value <= 0:
            raise ValueError(f"{info.field_name.upper()} must be greater than 0")
        return value

    @model_validator(mode="after")
    def _check_attempt_timeout_fits_deadline(self) -> Settings:
        if self.forward_attempt_timeout_seconds >= self.forward_deadline_seconds:
            raise ValueError(
                "FORWARD_ATTEMPT_TIMEOUT_SECONDS "
                f"({self.forward_attempt_timeout_seconds}) must be less than "
                f"FORWARD_DEADLINE_SECONDS ({self.forward_deadline_seconds}), otherwise a "
                "single slow attempt consumes the entire retry budget and "
                "FORWARD_MAX_ATTEMPTS never gets a chance to retry"
            )
        return self

    @property
    def hmac_key(self) -> bytes:
        return _decode_hmac_key(self.pokeproxy_hmac_key)

    @property
    def build_version(self) -> str:
        return _package_build_version()


class PokemonJSON(BaseModel):
    number: int
    name: str
    type_one: str
    type_two: str
    total: int
    hit_points: int
    attack: int
    defense: int
    special_attack: int
    special_defense: int
    speed: int
    generation: int
    legendary: bool


PokemonField = Literal[
    "number",
    "name",
    "type_one",
    "type_two",
    "total",
    "hit_points",
    "attack",
    "defense",
    "special_attack",
    "special_defense",
    "speed",
    "generation",
    "legendary",
]

Operator = Literal["==", "!=", ">", "<"]


class MatchCondition(BaseModel):
    field: PokemonField
    operator: Operator
    value: int | str | bool


class Rule(BaseModel):
    url: str
    reason: str
    conditions: list[MatchCondition]


def decode_pokemon(body: bytes) -> PokemonJSON:
    """Decode protobuf bytes into PokemonJSON. Raises ValueError on failure."""
    try:
        proto = pokemon_pb2.Pokemon()
        proto.ParseFromString(body)
    except DecodeError as e:
        raise ValueError(f"Invalid protobuf: {e}") from e

    if not proto.name:
        raise ValueError("Decoded protobuf has empty name — likely garbage input")

    return PokemonJSON(
        number=proto.number,
        name=proto.name,
        type_one=proto.type_one,
        type_two=proto.type_two,
        total=proto.total,
        hit_points=proto.hit_points,
        attack=proto.attack,
        defense=proto.defense,
        special_attack=proto.special_attack,
        special_defense=proto.special_defense,
        speed=proto.speed,
        generation=proto.generation,
        legendary=proto.legendary,
    )
