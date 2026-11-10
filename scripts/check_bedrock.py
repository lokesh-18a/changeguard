"""One-call check that AWS credentials and Amazon Nova on Bedrock work.
Run: python scripts/check_bedrock.py"""
import os, boto3
model = os.getenv("CHANGEGUARD_MODEL_ID", "amazon.nova-lite-v1:0")
client = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))
resp = client.converse(modelId=model,
                       messages=[{"role": "user", "content": [{"text": "In one sentence: what is a car insurance deductible?"}]}],
                       inferenceConfig={"maxTokens": 100, "temperature": 0})
print(resp["output"]["message"]["content"][0]["text"])
print("usage:", resp["usage"])
print("OK: Bedrock works with", model)
