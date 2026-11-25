from typing import Annotated
from uuid import UUID

from fastapi import Header
from pydantic import BaseModel, ConfigDict, Field, StringConstraints


Clave = Annotated[UUID, Header(alias="Idempotency-Key")]


class CrearPartida(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jugador: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Accion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Annotated[int, Field(strict=True, ge=1)]
