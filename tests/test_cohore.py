from core.LLM_cloud import LLMClient

llm = LLMClient()
print(llm.chat("You are a helpful assistant.", "What is the capital of France?"))