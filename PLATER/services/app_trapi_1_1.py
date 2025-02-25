"""FastAPI app."""

from fastapi import Body, Depends, FastAPI
from PLATER.models.models_trapi_1_1 import (MetaKnowledgeGraph, Message, ReasonerRequest)

from PLATER.services.util.graph_adapter import GraphInterface
from PLATER.services.util.question import Question
from PLATER.services.util.api_utils import get_graph_interface, construct_open_api_schema, get_example

# Mount open api at /1.1/openapi.json
APP_TRAPI_1_1 = FastAPI(openapi_url="/openapi.json", docs_url="/docs", root_path='/1.1')


async def get_meta_knowledge_graph(
        graph_interface: GraphInterface = Depends(get_graph_interface),
) -> MetaKnowledgeGraph:
    """Handle /meta_knowledge_graph."""
    response = await graph_interface.get_meta_kg()
    return response


async def reasoner_api(
        request: ReasonerRequest = Body(
            ...,
            example={"message": get_example("reasoner-trapi-1.1")},
        ),
        graph_interface: GraphInterface = Depends(get_graph_interface),
):
    """Handle TRAPI request."""
    request_json = request.dict(by_alias=True)
    question = Question(request_json["message"])
    response = await question.answer(graph_interface)
    request_json.update({'message': response})
    return request_json


APP_TRAPI_1_1.add_api_route(
    "/meta_knowledge_graph",
    get_meta_knowledge_graph,
    methods=["GET"],
    response_model=MetaKnowledgeGraph,
    summary="Meta knowledge graph representation of this TRAPI web service.",
    description="Returns meta knowledge graph representation of this TRAPI web service.",
    tags=["trapi"]
)

APP_TRAPI_1_1.add_api_route(
    "/query",
    reasoner_api,
    methods=["POST"],
    response_model=ReasonerRequest,
    summary="Query reasoner via one of several inputs.",
    description="",
    tags=["trapi"]
)

APP_TRAPI_1_1.openapi_schema = construct_open_api_schema(app=APP_TRAPI_1_1, trapi_version="1.1.2", prefix='/1.1')
