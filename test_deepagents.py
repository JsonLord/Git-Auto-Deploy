import os
from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI

# Mocking the environment for a test
os.environ["BLABLADOR_API_KEY"] = "test-key"

def test_tool(input_str: str) -> str:
    """A test tool."""
    return f"Tool received: {input_str}"

try:
    llm = ChatOpenAI(
        openai_api_key=os.environ["BLABLADOR_API_KEY"],
        openai_api_base="https://api.helmholtz-blablador.fz-juelich.de/v1",
        model_name="alias-code"
    )

    agent = create_deep_agent(
        model=llm,
        tools=[test_tool],
        system_prompt="You are a test agent."
    )
    print("Agent created successfully")
except Exception as e:
    import traceback
    traceback.print_exc()
