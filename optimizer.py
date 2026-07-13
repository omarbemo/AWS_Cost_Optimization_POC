import os
import json
from openai import OpenAI
import tf_json_convert as tfc
from tf_json_convert import terraform_to_resources, resources_to_terraform



MOCK_pricing_knowledge = {
    "aws_instance": """
[PLACEHOLDER FIGURES]
- t3.medium: ~$30/month on-demand
- t3.large: ~$60/month on-demand
- 1-year Savings Plan: ~20% discount on compute
- Reserved Instances: up to 40% discount for 1-year commit
- AWS Lambda: serverless compute, pay per invocation + duration, good for event-driven/intermittent workloads. ~$0.20 per million requests + ~$0.0000166667 per GB-second.
""",
    "aws_lambda_function": """
[PLACEHOLDER FIGURES]
- AWS Lambda: ~$0.20 per million requests + ~$0.0000166667 per GB-second.
- EC2 t3.medium: ~$30/month on-demand — may be cheaper for steady, always-on workloads instead of very high-frequency invocations.
""",
}

def attach_mock_cost(resource: dict) -> dict:
    MOCK_INSTANCE_COSTS = {"t3.medium": 30, "t3.large": 60, "t3.xlarge": 120}
    resource = dict(resource)
    if resource.get("resource_type") == "aws_instance":
        resource["monthly_cost_usd"] = MOCK_INSTANCE_COSTS.get(resource.get("instance_type"), 50)
    else:
        resource["monthly_cost_usd"] = 50
    return resource

def build_prompt(resource: dict) -> str:
    resource_type = resource.get("resource_type")
    facts = MOCK_pricing_knowledge.get(
        resource_type,
        "No specific pricing facts available — use general AWS knowledge."
    )

    schema_fields = list(tfc._COMMON_FIELDS.keys())
    supported_types = sorted(tfc.ALLOWED_TYPES)
    null_fields = ",\n    ".join(f'"{f}": null' for f in schema_fields)

    prompt = f"""You are a cloud cost optimization assistant.

Given this AWS resource and its current monthly cost, suggest ONE cheaper
alternative that achieves the same function. You may resize within the
same resource type, or switch to a different resource type entirely if
that's cheaper for the workload. If a field like 'workload_characteristics'
is present on the resource, use it to inform your suggestion.

Resource: {resource}

Relevant pricing/context facts (NOTE: placeholder figures for testing purposes):
{facts}

You may only suggest one of these resource types, since only these can be
converted back into real Terraform code: {supported_types}

Respond ONLY with valid JSON in this exact format, no other text:
{{
  "reason": "...",
  "estimated_monthly_cost_usd": 0,
  "new_resource": {{
    "resource_type": "<one of {supported_types}>",
    "resource_name": "<short name>",
    "region": "{resource.get('region')}",
    {null_fields}
  }}
}}

Fill in "new_resource" with your best values for the fields that are
actually relevant to the resource_type you chose. Leave fields that don't
apply to that resource_type as null. Only fill in values you can
reasonably justify from the pricing facts above or general AWS knowledge —
if you're not confident about an operational detail (like an IAM role ARN
or a deployment package path), leave it null rather than inventing one.
"""
    return prompt


def find_template_for_type(tf_path: str, resource_type: str, exclude_name: str = None):
    resources = terraform_to_resources(tf_path)
    for r in resources:
        if r.get("resource_type") == resource_type and r.get("resource_name") != exclude_name:
            return r
    return None


def fill_missing_fields(new_resource: dict, original_resource: dict, tf_path: str):
    resource_type = new_resource.get("resource_type")
    original_name = original_resource.get("resource_name")
    template = find_template_for_type(tf_path, resource_type, exclude_name=original_name)

    filled = dict(new_resource)
    provenance = {}

    for field in tfc._COMMON_FIELDS:
        if filled.get(field) is not None:
            provenance[field] = "llm"
            continue
        if original_resource.get(field) is not None:
            filled[field] = original_resource[field]
            provenance[field] = f"original:{original_name}"
            continue
        if template and template.get(field) is not None:
            filled[field] = template[field]
            provenance[field] = f"template:{template.get('resource_name')}"
        else:
            provenance[field] = "missing"

    return filled, provenance


def apply_suggestion_to_resource(llm_output: dict, original_resource: dict, tf_path: str):
    new_resource = dict(llm_output.get("new_resource", {}))
    if not new_resource.get("resource_type") or new_resource["resource_type"] not in tfc.ALLOWED_TYPES:
        return None, None

    filled, provenance = fill_missing_fields(new_resource, original_resource, tf_path)
    filled["monthly_cost_usd"] = llm_output.get("estimated_monthly_cost_usd")
    return filled, provenance


def optimize_entire_tf_file(tf_path: str, api_key: str, extra_hints: dict = None):
    if not api_key:
        raise ValueError("GROQ API Key is required")
        
    client = OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=api_key
    )
    
    parsed_resources = terraform_to_resources(tf_path)
    extra_hints = extra_hints or {}
    results = []

    for resource in parsed_resources:
        resource_with_cost = attach_mock_cost(resource)
        hint = extra_hints.get(resource_with_cost.get("resource_name"))
        if hint:
            resource_with_cost.update(hint)

        prompt = build_prompt(resource_with_cost)
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        raw_text = response.choices[0].message.content
        clean_text = raw_text.replace("```json", "").replace("```", "").strip()
        
        try:
            llm_output = json.loads(clean_text)
        except json.JSONDecodeError:
            print(f"Failed to decode JSON from LLM: {clean_text}")
            continue

        updated, provenance = apply_suggestion_to_resource(llm_output, resource_with_cost, tf_path)

        results.append({
            "original_name": resource.get("resource_name"),
            "reason": llm_output.get("reason"),
            "updated_resource": updated,
            "provenance": provenance,
        })

    updated_list = [r["updated_resource"] for r in results if r.get("updated_resource") is not None]
    
    new_terraform = ""
    if updated_list:
        try:
            new_terraform = resources_to_terraform(updated_list)
        except Exception as e:
            new_terraform = f"# Error generating terraform: {e}"
            
    return {
        "results": results,
        "new_terraform": new_terraform
    }
