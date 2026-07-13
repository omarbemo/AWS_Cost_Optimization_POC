!pip install python-hcl2 --break-system-packages -q
from google.colab import userdata
from openai import OpenAI

try:
    groq_key = userdata.get('GROQ_API_KEY')
except Exception:
    raise ValueError("Please add your 'GROQ_API_KEY' to the Colab Secrets (key icon) tab first.")

client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=groq_key
)
print("Groq client ready.")
import tf_json_convert as tfc
from tf_json_convert import terraform_to_resources, resources_to_terraform

print("Supported resource types:", sorted(tfc.ALLOWED_TYPES))
# MOCK / PLACEHOLDER pricing knowledge — for PoC reasoning tests only.
# Real project version: pull this data live from the AWS Pricing API instead.
MOCK_pricing_knowledge = {
    "aws_instance": """
[PLACEHOLDER FIGURES]
- t3.medium: ~$30/month on-demand
- t3.large: ~$60/month on-demand
- 1-year Savings Plan: ~20% discount on compute
- Reserved Instances: up to 40% discount for 1-year commit
- AWS Lambda: serverless compute, pay per invocation + duration, good for event-driven/intermittent workloads. ~$0.20 per million requests + ~$0.0000166667 per GB-second.
- AWS Fargate: serverless compute for containers, pay for vCPU/memory consumed. ~$0.04048 per vCPU-hour + ~$0.004445 per GB-hour. NOTE: Fargate is not yet supported by our Terraform writer (not a single resource type), so avoid suggesting it for now.
""",
    "aws_lambda_function": """
[PLACEHOLDER FIGURES]
- AWS Lambda: ~$0.20 per million requests + ~$0.0000166667 per GB-second.
- EC2 t3.medium: ~$30/month on-demand — may be cheaper for steady, always-on workloads instead of very high-frequency invocations.
""",
}

print("Loaded MOCK_pricing_knowledge (placeholder figures only, not live data).")
def attach_mock_cost(resource: dict) -> dict:
    """
    GENERIC placeholder-cost attacher — not hardcoded per exact instance
    type beyond this small lookup, and falls back sensibly for anything
    unlisted (including non-EC2 types). NOT real billing data — the full
    project replaces this with a real per-resource cost lookup.
    """
    MOCK_INSTANCE_COSTS = {"t3.medium": 30, "t3.large": 60, "t3.xlarge": 120}
    resource = dict(resource)
    if resource.get("resource_type") == "aws_instance":
        resource["monthly_cost_usd"] = MOCK_INSTANCE_COSTS.get(resource.get("instance_type"), 50)
    else:
        resource["monthly_cost_usd"] = 50  # generic placeholder for any other resource type
    return resource
def build_prompt(resource: dict) -> str:
    """
    GENERIC across every resource type tf_json_convert.py supports —
    built from tfc.ALLOWED_TYPES / tfc._COMMON_FIELDS, not hardcoded per
    service. Adding a new resource type to Omar Tarek's script needs NO
    changes here.

    Asks the LLM to return a full structured 'new_resource' dict shaped
    like tf_json_convert's schema, instead of a free-text sentence we'd
    otherwise have to regex-parse per service.
    """
    resource_type = resource.get("resource_type")
    facts = MOCK_pricing_knowledge.get(
        resource_type,
        "No specific pricing facts available — use general AWS knowledge."
    )

    schema_fields = list(tfc._COMMON_FIELDS.keys())
    supported_types = sorted(tfc.ALLOWED_TYPES)
    null_fields = ", ".join(f'"{f}": null' for f in schema_fields)

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
import re
import json


def find_template_for_type(tf_path: str, resource_type: str, exclude_name: str = None):
    """
    GENERIC across any resource_type tf_json_convert.py supports.
    Looks for an existing resource of the given type elsewhere in the
    .tf file, EXCLUDING the resource currently being replaced (so we
    don't grab an unrelated resource's data by accident).
    """
    resources = terraform_to_resources(tf_path)
    for r in resources:
        if r.get("resource_type") == resource_type and r.get("resource_name") != exclude_name:
            return r
    return None


def fill_missing_fields(new_resource: dict, original_resource: dict, tf_path: str):
    """
    GENERIC field-filling — no per-field, per-service conditions.
    Priority for any field the LLM left null:
      1. The ORIGINAL resource being replaced — its own real values
         (region, tags, and everything else if the type didn't change).
      2. A DIFFERENT existing resource of the NEW type elsewhere in the
         file, only needed when the type changed (e.g. EC2 -> Lambda
         needs 'role', which no EC2 resource has).
      3. Left unresolved ("missing") if neither source has it.
    """
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
    """
    GENERIC across any resource type in tfc.ALLOWED_TYPES — no if/elif
    per service. Returns (filled_resource, provenance), or (None, None)
    if the LLM picked a resource_type that isn't supported.
    """
    new_resource = dict(llm_output.get("new_resource", {}))
    if not new_resource.get("resource_type") or new_resource["resource_type"] not in tfc.ALLOWED_TYPES:
        return None, None

    filled, provenance = fill_missing_fields(new_resource, original_resource, tf_path)
    filled["monthly_cost_usd"] = llm_output.get("estimated_monthly_cost_usd")
    return filled, provenance


def print_provenance_report(provenance: dict):
    """Generic report — same code regardless of resource type or field names."""
    borrowed = {f: v for f, v in provenance.items() if v.startswith("template:") or v.startswith("original:")}
    unresolved = [f for f, v in provenance.items() if v == "missing"]

    if borrowed:
        print("Carried over from real Terraform data (review if the type changed):")
        for field, source in borrowed.items():
            kind, name = source.split(":", 1)
            label = "original resource" if kind == "original" else "a sibling resource"
            print(f"  - {field} <- {label} '{name}'")
    if unresolved:
        print("Not filled in (either genuinely required and unknown, or simply")
        print("not applicable to this resource type):")
        for field in unresolved:
            print(f"  - {field}")


def optimize_entire_tf_file(tf_path: str, extra_hints: dict = None):
    """
    THE SINGLE PIPELINE used by every test case in this notebook.
    GENERIC across however many resources are in the file, and however
    many resource types are supported — no per-resource, per-type code.

    extra_hints: optional dict of {resource_name: {field: value}} to
    merge onto specific resources before reasoning — a stand-in for real
    usage-pattern data (e.g. CloudWatch metrics) that isn't in the .tf
    file itself. Used only for testing cross-architecture reasoning.
    """
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
        llm_output = json.loads(clean_text)

        updated, provenance = apply_suggestion_to_resource(llm_output, resource_with_cost, tf_path)

        results.append({
            "original_name": resource.get("resource_name"),
            "reason": llm_output.get("reason"),
            "updated_resource": updated,
            "provenance": provenance,
        })

    return results


def print_and_generate(results):
    """Shared reporting + code-gen step, used after every optimize_entire_tf_file call."""
    for r in results:
        print(f"--- {r['original_name']} ---")
        print("Reason:", r["reason"])
        if r["updated_resource"] is None:
            print("-> LLM picked an unsupported resource_type, skipped.\n")
        else:
            print("-> New resource_type:", r["updated_resource"]["resource_type"])
            print_provenance_report(r["provenance"])
            print()

    updated_list = [r["updated_resource"] for r in results if r["updated_resource"] is not None]
    if updated_list:
        print("--- Combined generated Terraform ---")
        print(resources_to_terraform(updated_list))
    else:
        print("No resources produced a supported update.")
sample_tf_content = '''
provider "aws" {
  region = "us-east-1"
}

resource "aws_instance" "web" {
  ami           = "ami-0123456789"
  instance_type = "t3.large"

  root_block_device {
    volume_size = 100
  }
}
'''
with open("sample.tf", "w") as f:
    f.write(sample_tf_content)

results_a = optimize_entire_tf_file("sample.tf")
print_and_generate(results_a)
test_case_tf_content = '''
provider "aws" {
  region = "us-east-1"
}

resource "aws_instance" "batch_worker" {
  ami           = "ami-0abcdef1234567890"
  instance_type = "t3.xlarge"

  root_block_device {
    volume_size = 50
  }

  tags = {
    Name        = "nightly-batch-worker"
    Environment = "production"
  }
}
'''
with open("test_case_oversized.tf", "w") as f:
    f.write(test_case_tf_content)

hints = {
    "batch_worker": {
        "workload_characteristics": "intermittent, event-driven, spiky traffic, not continuously running 24/7"
    }
}

results_b = optimize_entire_tf_file("test_case_oversized.tf", extra_hints=hints)
print_and_generate(results_b)
multi_resource_tf_content = '''
provider "aws" {
  region = "us-east-1"
}

resource "aws_instance" "web" {
  ami           = "ami-0123456789"
  instance_type = "t3.large"
  root_block_device {
    volume_size = 100
  }
}

resource "aws_instance" "batch_worker" {
  ami           = "ami-0abcdef1234567890"
  instance_type = "t3.xlarge"
  root_block_device {
    volume_size = 50
  }
}
'''
with open("multi_resource.tf", "w") as f:
    f.write(multi_resource_tf_content)

results_c = optimize_entire_tf_file("multi_resource.tf")
print_and_generate(results_c)
test_with_role_tf_content = '''
provider "aws" {
  region = "us-east-1"
}

resource "aws_iam_role" "lambda_exec" {
  name               = "lambda-exec-role"
  assume_role_policy = "{}"
}

resource "aws_lambda_function" "price_predictor" {
  function_name = "stocksquares-price-predictor"
  filename      = "build/price_predictor.zip"
  role          = "arn:aws:iam::123456789012:role/lambda-exec-role"
  handler       = "handler.predict"
  runtime       = "python3.12"
  memory_size   = 512
  timeout       = 30
}

resource "aws_instance" "nightly_job" {
  ami           = "ami-0abcdef1234567890"
  instance_type = "t3.large"
  root_block_device {
    volume_size = 20
  }
}
'''
with open("test_with_role.tf", "w") as f:
    f.write(test_with_role_tf_content)

hints = {
    "nightly_job": {
        "workload_characteristics": "short nightly batch job, runs for a few minutes once a day"
    }
}

results_d = optimize_entire_tf_file("test_with_role.tf", extra_hints=hints)
print_and_generate(results_d)