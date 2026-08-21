from __future__ import annotations

import base64
from typing import Literal

from google.protobuf.message import DecodeError
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from pokeproxy.proto import pokemon_pb2


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=False, extra="ignore"
    )

    pokeproxy_hmac_key: str
    pokeproxy_config: str
    pokeproxy_port: int = 8000
    redis_url: str = "redis://localhost:6379/0"

    @property
    def hmac_key(self) -> bytes:
        return base64.b64decode(self.pokeproxy_hmac_key)


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
