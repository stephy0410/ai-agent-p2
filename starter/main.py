"""
Customer Support AI Agent — Starter Code
==========================================
Your task is to complete this file by implementing all sections marked
with # TODO comments.

Reference the step-by-step solution files and INSTRUCTIONS.md for guidance.
Do NOT copy the solution directly — work through each section yourself.

Run locally (after filling in config values):
  uv run main.py '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'

Deploy to AgentCore:
  agentcore deploy

Invoke deployed agent:
  agentcore invoke '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'
"""

# ── Imports ───────────────────────────────────────────────────────────────────
# These imports are provided. Do not remove them.
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamable_http_client
import argparse, json
import os, asyncio, boto3
from strands.hooks import (
    HookProvider, AfterInvocationEvent, HookRegistry, MessageAddedEvent,
)
import logging
import uuid
from typing import Dict
from bedrock_agentcore.tools.code_interpreter_client import code_session
from strands_tools.browser import AgentCoreBrowser
from pydantic import BaseModel, ValidationError
from typing import Optional

class DiscountBreakdown(BaseModel):
    order_total: float
    points_redeemed: Optional[int] = 0
    point_discount: Optional[float] = 0.0
    tier_discount: float
    final_total: float
    total_savings: Optional[float] = 0.0
    points_earned: Optional[int] = 0
    remaining_points: Optional[int] = 0
    note: Optional[str] = None


logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("CSAI_Agent")

# ── TODO 1 — App Initialisation ───────────────────────────────────────────────
# Create a BedrockAgentCoreApp instance.
# This registers the ASGI server for AgentCore deployment.
# There must be exactly one instance per deployment.
#
# Hint: app = BedrockAgentCoreApp()

# TODO: Create the BedrockAgentCoreApp instance
app = BedrockAgentCoreApp() # Replace this line


# Suppress interactive tool-consent prompts (required in headless deployments).
os.environ["BYPASS_TOOL_CONSENT"] = "true"


# ── TODO 2 — Configuration ────────────────────────────────────────────────────
# Replace the placeholder strings with your actual AWS resource values.
# You collected these in Part 1 of the INSTRUCTIONS.
#
# GATEWAY_URL format: https://<alias>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
# KB_ID       format: 10-character alphanumeric string from the KB console
# REGION:     your AWS region, e.g. "us-east-1"
# MEMORY_ID   format: shown in the AgentCore Memory console

GATEWAY_URL = "https://customersupportgateway-jzsslu9ijx.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"   
KB_ID       = "F4AKA5VZVZ"          
REGION      = "us-east-1"       
MEMORY_ID   = "CustomerSupportMemory-A2zm8JDTFI"       


# ── TODO 3 — Model and Clients ────────────────────────────────────────────────
# Create:
#   1. A BedrockModel using model_id "global.amazon.nova-2-lite-v1:0"
#   2. A MemoryClient with region_name=REGION
#   3. A boto3 client for the "bedrock-agent-runtime" service in REGION
#
# Hint: model = BedrockModel(model_id=model_id)

model_id = "global.amazon.nova-2-lite-v1:0"

# TODO: Create the BedrockModel instance
model = BedrockModel(model_id=model_id)

# TODO: Create the MemoryClient instance
memory_client = MemoryClient(region_name=REGION)

# TODO: Create the boto3 bedrock-agent-runtime client
_bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)


# ── TODO 4 — Namespace Helper ─────────────────────────────────────────────────
# Implement get_namespaces() to return a dict mapping strategy type to
# namespace template string.
#
# Steps:
#   1. Call mem_client.get_memory_strategies(memory_id) to get strategy list
#   2. Return a dict: { strategy["type"]: strategy["namespaces"][0] for each strategy }
#
# Example output:
#   { "SEMANTIC": "cs_agent/{actorId}/facts",
#     "USER_PREFERENCE": "cs_agent/{actorId}/preferences" }

def get_namespaces(mem_client: MemoryClient, memory_id: str) -> Dict:
    """Return a dict mapping strategy type → namespace template string."""
    strategies = mem_client.get_memory_strategies(memory_id=MEMORY_ID)
    return {strategy["type"]: strategy["namespaces"][0] for strategy in strategies }


# ── TODO 5 — Memory Hook ──────────────────────────────────────────────────────
# Implement MemoryHook, a HookProvider subclass that adds long-term memory.
#
# The class needs:
#   __init__(self, actor_id, session_id, memory_client, memory_id)
#     — store all four as instance attributes
#     — call get_namespaces() and store the result as self.namespaces
#
#   retrieve_customer_context(self, event: MessageAddedEvent)
#     — only runs for plain-text user messages (not tool results)
#     — for each strategy namespace, call memory_client.retrieve_memories(
#          memory_id, namespace (formatted with actorId), query, top_k=5)
#     — collect non-empty memory texts tagged with their strategy type
#     — if any memories found, prepend them to the user message as:
#          "Customer Context:\n<memories>\n\n<original_message>"
#
#   save_support_interaction(self, event: AfterInvocationEvent)
#     — walk the message list backwards to find the last plain-text user
#       query and the last assistant response
#     — call memory_client.create_event(memory_id, actor_id, session_id,
#          messages=[(customer_query, "USER"), (agent_response, "ASSISTANT")])
#
#   register_hooks(self, registry: HookRegistry)
#     — register retrieve_customer_context on MessageAddedEvent
#     — register save_support_interaction on AfterInvocationEvent

class MemoryHook(HookProvider):
    """Long-term memory hook for the customer support agent."""

    def __init__(
        self,
        actor_id: str,
        session_id: str,
        memory_client: MemoryClient,
        memory_id: str,
    ):
        # TODO: Store actor_id, session_id, memory_id, memory_client as attributes
        self.actor_id = actor_id
        self.session_id = session_id
        self.memory_client = memory_client
        self.memory_id = memory_id
        # TODO: Call get_namespaces() and store the result as self.namespaces
        self.namespaces = get_namespaces(memory_client, memory_id)

    def retrieve_customer_context(self, event: MessageAddedEvent):
        """Retrieve relevant memories and prepend them to the user message."""
        # TODO: Implement memory retrieval
        # Steps:
        #   1. Get the last message from event.agent.messages
        if not event.agent.messages:
            return
        last_message = event.agent.messages[-1]

        #   2. Check it is a user message and not a tool result
        if last_message.get("role") != "user":
            return
        content = last_message.get("content", [])
        if not isinstance(content, list) or len(content) == 0 or "toolResult" in content[0]:
            return
        #   3. Extract the user query text
        user_query = content[0].get("text", "")
        if not user_query:
            return
          # 4. For each namespace in self.namespaces, call retrieve_memories()
        memories = []
        for strategy_type, ns_template in self.namespaces.items():
            formatted_ns = ns_template.format(actorId=self.actor_id)
            try:
                records = self.memory_client.retrieve_memories(
                    memory_id=self.memory_id,
                    namespace=formatted_ns,
                    query=user_query,
                    top_k=5,
                )
                # 5. Collect non-empty memory texts with strategy type tags
                for record in records:
                    text = record.get("content", {}).get("text", "") if isinstance(record, dict) else str(record)
                    if text:
                        memories.append(f"[{strategy_type}] {text}")
            except Exception as e:
                logger.warning(f"Failed to retrieve memory for {strategy_type}: {e}")
        # 6. If any found, prepend them to the user message
        if memories:
            context = "Customer Context:\n" + "\n".join(memories)
            content[0]["text"] = f"{context}\n\n{user_query}"

    def save_support_interaction(self, event: AfterInvocationEvent):
        """Save the completed turn to memory after the agent responds."""
        # TODO: Implement memory saving
        # Steps:
        #   1. Get messages from event.agent.messages
        messages = event.agent.messages
        if not messages:
            return
        user_query = None
        agent_response = None
        #   2. Walk backwards to find the last user query (plain text) and the last assistant response
        for msg in reversed(messages):
            role = msg.get("role")
            content = msg.get("content", [])
            if not isinstance(content, list) or not content:
                continue
            if role == "assistant" and agent_response is None:
                for block in content:
                    if "text" in block:
                        agent_response = block["text"]
                        break
            elif role == "user" and user_query is None:
                if "toolResult" not in content[0] and "text" in content[0]:
                    user_query = content[0]["text"]
            if user_query and agent_response:
                break
         #   3. Call memory_client.create_event() with both messages
        if user_query and agent_response:
            try:
                self.memory_client.create_event(
                    memory_id=self.memory_id,
                    actor_id=self.actor_id,
                    session_id=self.session_id,
                    messages=[
                        (user_query, "USER"),
                        (agent_response, "ASSISTANT"),
                    ],
                )
            except Exception as e:
                logger.warning(f"Failed to save memory event: {e}")
       

    def register_hooks(self, registry: HookRegistry) -> None:  # type: ignore
        """Register both memory callbacks."""
        # TODO: Register retrieve_customer_context on MessageAddedEvent
        # TODO: Register save_support_interaction on AfterInvocationEvent
        registry.add_callback(MessageAddedEvent, self.retrieve_customer_context)
        registry.add_callback(AfterInvocationEvent, self.save_support_interaction)
        


# ── TODO 6 — Knowledge Base Tool ─────────────────────────────────────────────
# Implement search_knowledge_base(query) using the @tool decorator.
#
# Steps:
#   1. Guard: if KB_ID is empty return "Knowledge base not configured."
#   2. Call _bedrock_runtime.retrieve(
#          knowledgeBaseId=KB_ID,
#          retrievalQuery={"text": query}
#      )
#   3. Extract resp["retrievalResults"]; return a message if empty
#   4. Join the text chunks with "\n---\n" and return the result
#
# The docstring is the tool description — the model uses it to decide when
# to call this tool, so keep it clear and accurate.

@tool
def search_knowledge_base(query: str) -> str:
    """
    Search the Amazon product catalog and support knowledge base.
    Use this for product specifications, return policies, warranty
    information, loyalty program details, and order status definitions.

    Args:
        query: The question or topic to search for

    Returns:
        Relevant information retrieved from the knowledge base
    """
    # 1. Guard: if KB_ID is empty return error message
    if not KB_ID or KB_ID == "<kbid>":
        return "Knowledge base not configured."

    try:
        # 2. Call _bedrock_runtime.retrieve
        resp = _bedrock_runtime.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": query},
        )
        # 3. Extract resp["retrievalResults"]; return a message if empty
        results = resp.get("retrievalResults", [])
        if not results:
            return "No relevant information found in the knowledge base."

        # 4. Join the text chunks with "\n---\n" and return the result
        chunks = [
            r.get("content", {}).get("text", "")
            for r in results
            if r.get("content", {}).get("text")
        ]
        return "\n---\n".join(chunks) if chunks else "No relevant information found."
    except Exception as e:
        logger.error(f"Error querying knowledge base: {e}")
        return f"Error retrieving from knowledge base: {e}"


# ── TODO 7 — Loyalty Discount Tool (Code Interpreter) ────────────────────────
# Implement calculate_loyalty_discount() using the @tool decorator.
#
# The tool must:
#   1. Build a self-contained Python code string that:
#        • Defines earn_rates: {"standard": 1, "device": 2, "fresh": 5}
#        • Defines tier_rates: {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}
#        • Calculates points_redeemed (floor to nearest 500, cap at 50% of order)
#        • Calculates tier_discount (applied to subtotal after points)
#        • Calculates final_total, total_savings, points_earned, remaining_points
#        • Prints a JSON result dict
#   2. Execute the code with code_session(REGION).invoke("executeCode", {...})
#      using language="python" and clearContext=True
#   3. Return the first result event as a JSON string
#   4. Include a fallback that computes only the tier discount if the
#      Code Interpreter is unavailable

@tool
def calculate_loyalty_discount(
    loyalty_points: int,
    tier: str,
    order_total: float,
    product_category: str = "standard",
) -> str:
    """
    Calculate the loyalty discount for a customer order using the
    AgentCore Code Interpreter. Runs exact arithmetic in a secure sandbox.

    Args:
        loyalty_points:   Customer's current points balance
        tier:             Customer tier — Silver, Gold, or Platinum
        order_total:      Order total in USD
        product_category: standard, device, or fresh

    Returns:
        Full discount breakdown and final price
    """
    # 1. Build the code string
    code = f"""
import json

earn_rates = {{"standard": 1, "device": 2, "fresh": 5}}
tier_rates = {{"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}}

order_total = {order_total}
loyalty_points = {loyalty_points}
tier = "{tier}"
category = "{product_category}"

# 100 points = $1. Points can cover at most 50% of order value
max_points_redeemable = int((order_total * 0.5) * 100)
points_to_use = min(loyalty_points, max_points_redeemable)
# Floor to nearest 500 points
points_redeemed = (points_to_use // 500) * 500
point_discount = points_redeemed / 100.0

subtotal_after_points = max(0.0, order_total - point_discount)
tier_rate = tier_rates.get(tier, 0.0)
tier_discount = subtotal_after_points * tier_rate
final_total = subtotal_after_points - tier_discount
total_savings = point_discount + tier_discount

points_earned = int(final_total * earn_rates.get(category, 1))
remaining_points = loyalty_points - points_redeemed + points_earned

result = {{
    "order_total": round(order_total, 2),
    "points_redeemed": points_redeemed,
    "point_discount": round(point_discount, 2),
    "tier_discount": round(tier_discount, 2),
    "final_total": round(final_total, 2),
    "total_savings": round(total_savings, 2),
    "points_earned": points_earned,
    "remaining_points": remaining_points
}}
print(json.dumps(result))
"""

    try:
        # 2. Execute the code using code_session
        with code_session(REGION) as session:
            response = session.invoke(
                "executeCode",
                {"code": code, "language": "python", "clearContext": True},
            )
            # 3. Return the first result event as JSON string
            for event in response.get("stream", []):
                if "result" in event:
                    stdout = event["result"].get("structuredContent", {}).get("stdout")
                    if stdout:
                        try:
                            # 4. Parse and validate with Pydantic
                            data = json.loads(stdout)
                            validated = DiscountBreakdown(**data)
                            return validated.model_dump_json(indent=2)
                        except ValidationError as ve:
                            logger.error(f"Pydantic Validation Error: {ve}")
                            return stdout
                        except json.JSONDecodeError:
                            return stdout
                    return json.dumps(event["result"])
            return "No output from code execution."

    except Exception as e:
        # 4. Fallback calculation if Code Interpreter is unavailable
        logger.warning(f"Code Interpreter unavailable, using fallback: {e}")
        tier_rate = {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}.get(tier, 0.0)
        tier_discount = order_total * tier_rate
        final_total = order_total - tier_discount
        fallback_data = {
            "order_total": round(order_total, 2),
            "tier_discount": round(tier_discount, 2),
            "final_total": round(final_total, 2),
            "note": "Fallback calculation (tier discount only, Code Interpreter unavailable)"
        }
        return DiscountBreakdown(**fallback_data).model_dump_json(indent=2)


# ── TODO 8 — Agent Entrypoint ─────────────────────────────────────────────────
# Implement the invoke() function decorated with @app.entrypoint.

SYSTEM_PROMPT = """You are a polite, helpful, and efficient customer support agent for an Amazon store.
You have access to tools that allow you to:
1. Search the product catalog, policies, and support knowledge base (search_knowledge_base).
2. Check customer details, order statuses, and past customer orders via the order tracker tools.
3. Process refunds, check refund statuses, and generate return shipping labels via the refund processor tools.
4. Calculate precise loyalty discounts and points using the code interpreter (calculate_loyalty_discount).
5. Look up live web information when needed (browser).

Guidelines:
- Always be courteous and professional.
- Consult the knowledge base for product specifications, return policies, and general store FAQs.
- Use the order tracker and refund processor tools for order/refund inquiries.
- Always use the calculate_loyalty_discount tool for loyalty discount arithmetic to ensure accuracy.
- Provide clear and concise responses to customer questions.
"""

@app.entrypoint
async def invoke(payload, context=None):
    """
    Main handler called by AgentCore for every incoming request.

    Expected payload keys:
      prompt      (str, required) — the customer's message
      customer_id (str, optional) — unique customer identifier
      session_id  (str, optional) — session identifier; generated if absent
    """
    try:
        # 1. Extract user_input, actor_id, and session_id from the payload
        user_input = payload.get("prompt", "")
        actor_id = payload.get("customer_id", "default_customer")
        session_id = payload.get("session_id") or str(uuid.uuid4())

        # 2. Instantiate MemoryHook for this actor/session
        mem_hook = MemoryHook(
            actor_id=actor_id,
            session_id=session_id,
            memory_client=memory_client,
            memory_id=MEMORY_ID,
        )

        # 3. Instantiate AgentCoreBrowser(region=REGION)
        agent_core_browser = AgentCoreBrowser(region=REGION)

        # 4. Build the base tools list
        tools = [
            search_knowledge_base,
            calculate_loyalty_discount,
            agent_core_browser.browser,
        ]

        # 5. Connect to the Gateway via MCPClient, load gateway_tools, extend tools list
        with MCPClient(
            transport_callable=lambda: streamable_http_client(GATEWAY_URL)
        ) as mcp_client:
            gateway_tools = mcp_client.list_tools_sync()
            all_tools = tools + list(gateway_tools)

            # 6. Create and invoke the Agent with all tools, hooks, and system_prompt
            agent = Agent(
                model=model,
                tools=all_tools,
                system_prompt=SYSTEM_PROMPT,
                hooks=[mem_hook],
            )

            response = agent(user_input)

        # 7. Return the text from the response
        if hasattr(response, "messages") and response.messages:
            last_msg = response.messages[-1]
            content = last_msg.get("content", [])
            if isinstance(content, list) and content:
                for block in content:
                    if isinstance(block, dict) and "text" in block:
                        return block["text"]
            elif isinstance(content, str):
                return content
        elif hasattr(response, "text"):
            return response.text
        return str(response)

    except Exception as e:
        logger.error(f"Error during agent invocation: {e}", exc_info=True)
        return f"An error occurred while processing your request: {e}"


# ── CLI entry point (do not modify) ──────────────────────────────────────────
def main():
    """Run one invocation from the command line for local testing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=str)
    args = parser.parse_args()
    response = asyncio.run(invoke(json.loads(args.payload)))
    print(response)


if __name__ == "__main__":
    app.run()
    # Uncomment the line below and comment app.run() for local CLI testing:
    # main()
