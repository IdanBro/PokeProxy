from __future__ import annotations

import base64
from typing import Literal

from google.protobuf.message import DecodeError
from pydantic import BaseModel, field_validator
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

    @field_validator("pokeproxy_hmac_key")
    @classmethod
    def _check_hmac_key(cls, raw: str) -> str:
        _decode_hmac_key(raw)
        return raw

    @field_validator("forward_max_attempts")
    @classmethod
    def _check_forward_max_attempts(cls, value: int) -> int:
        if value < 1:
            raise ValueError("POKEPROXY_FORWARD_MAX_ATTEMPTS must be at least 1")
        return value

    @field_validator("forward_deadline_seconds")
    @classmethod
    def _check_forward_deadline_seconds(cls, value: float) -> float:
        if value <= 0:
            raise ValueError(
                "POKEPROXY_FORWARD_DEADLINE_SECONDS must be greater than 0"
            )
        return value

    @property
    def hmac_key(self) -> bytes:
        return _decode_hmac_key(self.pokeproxy_hmac_key)


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
