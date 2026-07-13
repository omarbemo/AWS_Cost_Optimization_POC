#!/usr/bin/env python3
"""
tf_json_convert.py
-------------------
Converts between Terraform (.tf) and the pricing-agent JSON schema, in
either direction:

    Terraform (.tf)  --->  JSON list of resources
    JSON             --->  Terraform (.tf)

Supported resource types (matching the scope of MOCK_pricing_knowledge):
    - aws_instance          (EC2)
    - aws_lambda_function   (Lambda)

Anything else in a .tf file (aws_s3_bucket, aws_db_instance, aws_iam_role,
Fargate/ECS, ...) is skipped when reading. Fargate isn't supported yet since
it isn't a single Terraform resource type — it's an ECS task
definition + service pair with its own container-definition schema; ask if
you want that added.

JSON schema — one dict per resource. Fields not relevant to a given
resource_type are left null/empty (e.g. a Lambda entry has ami=null,
an EC2 entry has runtime=null):

    # EC2
    {
        "resource_type": "aws_instance",
        "resource_name": "web",
        "instance_type": "t3.large",
        "region": "us-east-1",
        "storage_gb": 100,
        "ami": "ami-0123456789",
        "availability_zone": null,
        "key_name": null,
        "subnet_id": null,
        "security_group_ids": [],
        "volume_type": null,
        "function_name": null,
        "runtime": null,
        "handler": null,
        "memory_mb": null,
        "timeout_seconds": null,
        "role": null,
        "filename": null,
        "environment": {},
        "tags": {}
    }

    # Lambda
    {
        "resource_type": "aws_lambda_function",
        "resource_name": "image_resizer",
        "region": "us-east-1",
        "function_name": "image-resizer",
        "runtime": "python3.12",
        "handler": "index.handler",
        "memory_mb": 512,
        "timeout_seconds": 30,
        "role": "arn:aws:iam::123456789012:role/lambda-exec",
        "filename": "build/function.zip",
        "environment": {"BUCKET_NAME": "my-bucket"},
        "tags": {},
        "instance_type": null,
        "storage_gb": null,
        "ami": null,
        "availability_zone": null,
        "key_name": null,
        "subnet_id": null,
        "security_group_ids": [],
        "volume_type": null
    }

To switch a resource from EC2 to Lambda (or vice versa) in the JSON, change
"resource_type" and fill in that type's fields — the other type's fields
can be left null, they're ignored on generation.

NOTE: "filename" (the path to the zipped deployment package) is required by
the real aws_lambda_function resource (or s3_bucket/s3_key/image_uri as an
alternative) for `terraform apply` to work. This converter doesn't have
that file, so it's captured as a plain field you must set yourself before
applying — the generated block will be syntactically valid either way, but
terraform validate/apply will fail without a real deployment package
reference.

Direction is auto-detected from the input file's extension (.tf vs .json),
or can be forced with --direction.

Requires: pip install python-hcl2 --break-system-packages
"""

import argparse
import json
import sys

import hcl2
from hcl2.utils import SerializationOptions

# strip_string_quotes: newer python-hcl2 (8.x) keeps the literal quote
#   characters in string values/keys by default (e.g. '"t3.large"'); we
#   want the clean string instead.
# explicit_blocks: turns off the injected "__is_block__" marker key that
#   newer python-hcl2 adds to every block dict.
_HCL2_OPTIONS = SerializationOptions(strip_string_quotes=True, explicit_blocks=False)

# Resource types recognized in either direction.
ALLOWED_TYPES = {"aws_instance", "aws_lambda_function"}

# Fields present on every resource dict regardless of type, so JSON entries
# have a consistent shape (unused fields for a given type are left null/empty).
_COMMON_FIELDS = {
    "instance_type": None,
    "storage_gb": None,
    "ami": None,
    "availability_zone": None,
    "key_name": None,
    "subnet_id": None,
    "security_group_ids": [],
    "volume_type": None,
    "function_name": None,
    "runtime": None,
    "handler": None,
    "memory_mb": None,
    "timeout_seconds": None,
    "role": None,
    "filename": None,
    "environment": {},
    "tags": {},
}


def _with_common_fields(overrides: dict) -> dict:
    """Merge type-specific fields on top of the full common-field template
    so every resource dict, regardless of type, has every key."""
    merged = dict(_COMMON_FIELDS)
    merged.update(overrides)
    return merged

# Sensible default used when a value isn't explicitly set in the HCL
# (mirrors AWS's real default).
DEFAULT_ROOT_VOLUME_GB = 8  # AWS default root EBS volume size for aws_instance


# ---------------------------------------------------------------------------
# Terraform -> JSON
# ---------------------------------------------------------------------------

def _unwrap(value):
    """
    python-hcl2 often wraps block bodies / repeated attributes in a
    single-item list, e.g. {"root_block_device": [{"volume_size": 100}]}.
    This pulls the real value out when that happens.
    """
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def _get_region(parsed: dict) -> str | None:
    """
    Pull the AWS region out of a `provider "aws" { region = "..." }` block,
    if one exists. Falls back to None if not found (e.g. region is set via
    a variable that can't be resolved statically).
    """
    for provider_block in parsed.get("provider", []):
        aws_cfg = provider_block.get("aws")
        if aws_cfg is None:
            continue
        aws_cfg = _unwrap(aws_cfg)
        region = aws_cfg.get("region")
        if region is not None:
            return _unwrap(region)
    return None


def _extract_security_group_ids(body: dict) -> list:
    """
    Instances can reference security groups either via `vpc_security_group_ids`
    (list of IDs, typical for VPC instances) or the older `security_groups`
    (list of names, EC2-Classic / some test setups). Normalize both into one
    field so we don't lose data from either style.
    """
    ids = _unwrap(body.get("vpc_security_group_ids"))
    if ids:
        return ids if isinstance(ids, list) else [ids]
    names = _unwrap(body.get("security_groups"))
    if names:
        return names if isinstance(names, list) else [names]
    return []


def _extract_aws_instance(name: str, body: dict, region: str | None) -> dict:
    storage_gb = DEFAULT_ROOT_VOLUME_GB
    volume_type = None
    root_block_device = body.get("root_block_device")
    if root_block_device is not None:
        rbd = _unwrap(root_block_device)
        if isinstance(rbd, dict):
            if rbd.get("volume_size") is not None:
                storage_gb = _unwrap(rbd["volume_size"])
            if rbd.get("volume_type") is not None:
                volume_type = _unwrap(rbd["volume_type"])

    return _with_common_fields({
        "resource_type": "aws_instance",
        "resource_name": name,
        "region": region,  # region lives on the provider block, not the resource
        "instance_type": _unwrap(body.get("instance_type")),
        "storage_gb": storage_gb,
        "ami": _unwrap(body.get("ami")),
        "availability_zone": _unwrap(body.get("availability_zone")),
        "key_name": _unwrap(body.get("key_name")),
        "subnet_id": _unwrap(body.get("subnet_id")),
        "security_group_ids": _extract_security_group_ids(body),
        "volume_type": volume_type,
        "tags": _unwrap(body.get("tags")) or {},
    })


def _extract_environment_variables(body: dict) -> dict:
    """`environment { variables = { KEY = "value" } }` -> {"KEY": "value"}."""
    env_block = body.get("environment")
    if env_block is None:
        return {}
    env_block = _unwrap(env_block)
    if not isinstance(env_block, dict):
        return {}
    variables = _unwrap(env_block.get("variables"))
    return variables if isinstance(variables, dict) else {}


def _extract_aws_lambda_function(name: str, body: dict, region: str | None) -> dict:
    return _with_common_fields({
        "resource_type": "aws_lambda_function",
        "resource_name": name,
        "region": region,
        "function_name": _unwrap(body.get("function_name")),
        "runtime": _unwrap(body.get("runtime")),
        "handler": _unwrap(body.get("handler")),
        "memory_mb": _unwrap(body.get("memory_size")),
        "timeout_seconds": _unwrap(body.get("timeout")),
        "role": _unwrap(body.get("role")),
        "filename": _unwrap(body.get("filename")),
        "environment": _extract_environment_variables(body),
        "tags": _unwrap(body.get("tags")) or {},
    })


_EXTRACTORS = {
    "aws_instance": _extract_aws_instance,
    "aws_lambda_function": _extract_aws_lambda_function,
}


def terraform_to_resources(tf_path: str) -> list[dict]:
    with open(tf_path, "r") as f:
        parsed = hcl2.load(f, serialization_options=_HCL2_OPTIONS)

    region = _get_region(parsed)
    resources = []

    for resource_block in parsed.get("resource", []):
        for resource_type, named_bodies in resource_block.items():
            extractor = _EXTRACTORS.get(resource_type)
            if extractor is None:
                continue  # unsupported resource type -> skip
            for resource_name, body in named_bodies.items():
                body = _unwrap(body)
                resources.append(extractor(resource_name, body, region))

    return resources


# ---------------------------------------------------------------------------
# JSON -> Terraform
# ---------------------------------------------------------------------------

def _hcl_string(value) -> str:
    """Render a Python string as a double-quoted HCL string literal."""
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _tags_block_lines(tags: dict, indent: str = "  ") -> list:
    if not tags:
        return []
    lines = ["", f"{indent}tags = {{"]
    for k, v in tags.items():
        lines.append(f'{indent}  {k} = {_hcl_string(v)}')
    lines.append(f"{indent}}}")
    return lines


def _ec2_to_hcl(res: dict) -> str:
    name = res.get("resource_name") or "instance"
    lines = [f'resource "aws_instance" "{name}" {{']

    if res.get("ami"):
        lines.append(f'  ami                    = {_hcl_string(res["ami"])}')
    if res.get("instance_type"):
        lines.append(f'  instance_type          = {_hcl_string(res["instance_type"])}')
    if res.get("availability_zone"):
        lines.append(f'  availability_zone      = {_hcl_string(res["availability_zone"])}')
    if res.get("key_name"):
        lines.append(f'  key_name               = {_hcl_string(res["key_name"])}')
    if res.get("subnet_id"):
        lines.append(f'  subnet_id              = {_hcl_string(res["subnet_id"])}')

    sg_ids = res.get("security_group_ids") or []
    if sg_ids:
        sg_list = ", ".join(_hcl_string(s) for s in sg_ids)
        lines.append(f'  vpc_security_group_ids = [{sg_list}]')

    # storage_gb always has a value (default 8), so always emit the block.
    lines.append("")
    lines.append("  root_block_device {")
    lines.append(f'    volume_size = {res.get("storage_gb") or DEFAULT_ROOT_VOLUME_GB}')
    if res.get("volume_type"):
        lines.append(f'    volume_type = {_hcl_string(res["volume_type"])}')
    lines.append("  }")

    lines.extend(_tags_block_lines(res.get("tags") or {}))
    lines.append("}")
    return "\n".join(lines)


def _lambda_to_hcl(res: dict) -> str:
    name = res.get("resource_name") or "function"
    lines = [f'resource "aws_lambda_function" "{name}" {{']

    if res.get("filename"):
        lines.append(f'  filename      = {_hcl_string(res["filename"])}')
    else:
        # Real Terraform requires filename, s3_bucket/s3_key, or image_uri
        # to point at an actual deployment package. We don't have one, so
        # leave a clear placeholder rather than emitting invalid/silent HCL.
        lines.append('  filename      = "REPLACE_ME.zip" # TODO: set your deployment package (filename, s3_bucket/s3_key, or image_uri)')
    if res.get("function_name"):
        lines.append(f'  function_name = {_hcl_string(res["function_name"])}')
    if res.get("role"):
        lines.append(f'  role          = {_hcl_string(res["role"])}')
    if res.get("handler"):
        lines.append(f'  handler       = {_hcl_string(res["handler"])}')
    if res.get("runtime"):
        lines.append(f'  runtime       = {_hcl_string(res["runtime"])}')
    if res.get("memory_mb"):
        lines.append(f'  memory_size   = {res["memory_mb"]}')
    if res.get("timeout_seconds"):
        lines.append(f'  timeout       = {res["timeout_seconds"]}')

    env = res.get("environment") or {}
    if env:
        lines.append("")
        lines.append("  environment {")
        lines.append("    variables = {")
        for k, v in env.items():
            lines.append(f'      {k} = {_hcl_string(v)}')
        lines.append("    }")
        lines.append("  }")

    lines.extend(_tags_block_lines(res.get("tags") or {}))
    lines.append("}")
    return "\n".join(lines)


_GENERATORS = {
    "aws_instance": _ec2_to_hcl,
    "aws_lambda_function": _lambda_to_hcl,
}


def _resource_to_hcl(res: dict) -> str:
    generator = _GENERATORS.get(res.get("resource_type"))
    if generator is None:
        raise ValueError(
            f"Unsupported resource_type {res.get('resource_type')!r} "
            f"(supported: {', '.join(sorted(ALLOWED_TYPES))})"
        )
    return generator(res)


def resources_to_terraform(resources: list[dict]) -> str:
    resources = [r for r in resources if r.get("resource_type") in ALLOWED_TYPES]
    if not resources:
        raise ValueError(
            f"No resources with a supported resource_type ({', '.join(sorted(ALLOWED_TYPES))}) found in JSON."
        )

    blocks = []

    region = next((r.get("region") for r in resources if r.get("region")), None)
    if region:
        blocks.append(f'provider "aws" {{\n  region = {_hcl_string(region)}\n}}')

    blocks.extend(_resource_to_hcl(r) for r in resources)

    return "\n\n".join(blocks) + "\n"


def json_to_terraform(json_path: str) -> str:
    with open(json_path, "r") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = [data]
    return resources_to_terraform(data)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _detect_direction(input_path: str) -> str:
    if input_path.endswith(".tf"):
        return "to-json"
    if input_path.endswith(".json"):
        return "to-tf"
    raise ValueError(
        f"Can't auto-detect direction from '{input_path}' "
        "(expected a .tf or .json extension). Use --direction to force it."
    )


def main():
    parser = argparse.ArgumentParser(
        description="Convert between Terraform (.tf) and the pricing-agent JSON schema, in either direction."
    )
    parser.add_argument("input", help="Path to input .tf or .json file")
    parser.add_argument(
        "-o", "--output", help="Path to output file (default: resources.json or resources.tf)"
    )
    parser.add_argument(
        "--direction",
        choices=["to-json", "to-tf"],
        help="Force conversion direction instead of auto-detecting from the input's extension",
    )
    args = parser.parse_args()

    try:
        direction = args.direction or _detect_direction(args.input)

        if direction == "to-json":
            resources = terraform_to_resources(args.input)
            output_path = args.output or "resources.json"
            with open(output_path, "w") as f:
                json.dump(resources, f, indent=4)
            print(f"Extracted {len(resources)} resource(s) -> {output_path}")

        else:  # to-tf
            hcl_text = json_to_terraform(args.input)
            output_path = args.output or "resources.tf"
            with open(output_path, "w") as f:
                f.write(hcl_text)
            print(f"Wrote Terraform to {output_path}")

    except FileNotFoundError:
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
