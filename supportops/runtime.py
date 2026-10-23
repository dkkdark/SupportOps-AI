"""The same persistent investigation graphs for HTTP requests and workers."""

from contextlib import contextmanager, ExitStack
from types import SimpleNamespace
from supportops.agent import build_agent_graph
from supportops.workflow import build_graph


@contextmanager
def graph_runtime(
    config, sessions, orders_transport=None, llm_transport=None, vector_transport=None
):
    with ExitStack() as stack:
        if config.database_url.startswith("postgresql"):
            from langgraph.checkpoint.postgres import PostgresSaver

            cp = stack.enter_context(
                PostgresSaver.from_conn_string(
                    config.database_url.replace(
                        "postgresql+psycopg://", "postgresql://"
                    )
                )
            )
        else:
            from langgraph.checkpoint.sqlite import SqliteSaver
            from sqlalchemy.engine import make_url

            cp = stack.enter_context(
                SqliteSaver.from_conn_string(
                    str(make_url(config.database_url).database) + ".checkpoints"
                )
            )
        yield SimpleNamespace(
            graph=build_graph(config, sessions, cp, orders_transport, llm_transport),
            agent_graph=build_agent_graph(
                config, sessions, cp, llm_transport, vector_transport
            ),
        )
