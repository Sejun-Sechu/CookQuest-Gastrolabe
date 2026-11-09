"""Fridge discovery and Gastrolabe integration endpoints."""
import os
from typing import Annotated

from fastapi import APIRouter, Depends
from flask import g, jsonify, request
from starlette.middleware.wsgi import WSGIMiddleware

from app.recipes.discovery import (DiscoveryBatch, DiscoveryRequest, RecipeMethod, DiscoveryService, get_discovery)
from app.recipes.ingredients import inventory_options
from fastapi import HTTPException
from app.fridge import api as server
from app.fridge import ai as gemini_client

router = APIRouter(prefix="/api/v1/integration", tags=["integration"])


@router.get("/ingredients")
def ingredient_options():
    """Shared normalization aliases and explicit opt-in inventory suggestions."""
    from app.fridge.images import suggestions
    options = inventory_options()
    options["suggestions"] = suggestions(options["suggestions"])
    options["suggestion_source"] = "Local vocabulary + TheMealDB (when available)"
    return options


@router.post("/recipes/discover", response_model=DiscoveryBatch)
def discover_recipes(body: DiscoveryRequest, discovery: Annotated[DiscoveryService, Depends(get_discovery)]):
    """Return source-backed metadata only. Replace the list UI without changing this contract."""
    batch = discovery.discover(body)
    if batch.recipes:
        from app.gastrolabe.store import get_map_store
        token = get_map_store().create(batch)
        batch = batch.model_copy(update={"map_session_id": token, "map_url": f"/gastrolabe/?session={token}"})
    return batch


@router.get("/recipes/{recipe_id}/method", response_model=RecipeMethod,
            responses={404: {"description": "Unknown or expired recipe"},
                       503: {"description": "Original recipe source unavailable"}})
def recipe_method(recipe_id: str, discovery: Annotated[DiscoveryService, Depends(get_discovery)]):
    """Return cached source instructions, not AI assessment or photo checkpoints."""
    return discovery.method(recipe_id)


@router.post("/recipes/{recipe_id}/check",
             responses={404: {"description": "Unknown or expired recipe"},
                        409: {"description": "An ingredient is no longer in the confirmed inventory"}})
def check_recipe(recipe_id: str, body: DiscoveryRequest, discovery: Annotated[DiscoveryService, Depends(get_discovery)]):
    """Cook gate: recheck the player's current confirmed inventory before cooking starts."""
    result = discovery.recheck(recipe_id, body)
    if not result["eligible"]:
        names = ", ".join(result["missing"])
        raise HTTPException(409, detail={"code": "ingredients_missing", "missing": result["missing"],
                                         "message": f"Too many missing essentials or unresolved ingredient roles: {names}. Update your ingredient list and try again."})
    return {"id": recipe_id, "ready": True}


def fridge_app():
    # A distinct Flask instance avoids changing standalone server.py or its tests.
    from flask import Flask
    app = Flask("integrated_fridge", static_folder=None)
    app.config.update(server.app.config)
    for rule in server.app.url_map.iter_rules():
        app.add_url_rule(str(rule), rule.endpoint, server.app.view_functions[rule.endpoint],
                         methods=rule.methods)
    for code, handlers in server.app.error_handler_spec.get(None, {}).items():
        for exception, handler in handlers.items():
            app.register_error_handler(exception, handler)

    @app.before_request
    def guard():
        g.key_context = gemini_client.integrated_request.set(True)
        if request.path == "/api/status":
            return None
        if request.path.startswith("/api/"):
            body = request.get_json(silent=True) or {}
            if not isinstance(body, dict):
                return jsonify(error="Expected a JSON object.", code="invalid_input"), 400
            if body.get("demo"):
                return jsonify(error="Sample fallback is disabled in the integrated service.", code="demo_disabled"), 400
            if request.path == "/api/recipe" and request.method == "POST":
                try:
                    result = get_discovery().discover(DiscoveryRequest.model_validate(body))
                    return jsonify(status="ok", **result.model_dump())
                except HTTPException as error:
                    return jsonify(error=error.detail["message"], code=error.detail["code"]), error.status_code
                except ValueError:
                    return jsonify(error="Invalid discovery request.", code="bad_request"), 400
            if not os.environ.get("FRIDGE_GEMINI_API_KEY") or os.environ.get("FRIDGE_GEMINI_FREE_TIER_CONFIRMED", "").lower() != "true":
                return jsonify(error="Configure the fridge Gemini key and confirm Free Tier on the server.", code="fridge_not_configured"), 503

    @app.teardown_request
    def reset(_error):
        if hasattr(g, "key_context"):
            gemini_client.integrated_request.reset(g.key_context)

    return WSGIMiddleware(app)
