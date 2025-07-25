import json
import os

# ruff: noqa: I001
import aws_cdk as core
from aws_cdk import (
    Fn,
    Duration,
    aws_apigateway as apigw,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_lambda_event_sources as lambda_event_source,
    aws_logs as logs,
    aws_sqs as sqs,
    aws_s3_assets as s3_assets,
)
from constructs import Construct

from wellapi.applications import Lambda, WellApi
from wellapi.build.packager import OpenAPIBundling, PackageBundling
from wellapi.utils import import_app

OPENAPI_FILE = "openapi-spec.json"
APP_LAYOUT_FILE = "app_content.zip"
DEP_LAYOUT_FILE = "layer_content.zip"


class WellApiCDK(Construct):
    """
    This class is used to create a WellAPI using AWS CDK.
    """

    def __init__(
        self,
        scope: Construct,
        id_: str,
        *,
        app_srt: str,
        handlers_dir: str,
        vpc = None,
        vpc_subnets = None,
        sg = None,
        environment: dict | None = None,
        cors: bool = False,
        cache_enable: bool = False,
        log_enable: bool = False,
        prefix: str | None = None,
    ) -> None:
        super().__init__(scope, id_)

        if prefix is None:
            prefix = ""

        self.app_srt = app_srt
        self.handlers_dir = os.path.abspath(handlers_dir)

        role_name = prefix + "WellApiRole"
        api_role = iam.Role(
            self,
            role_name,
            assumed_by=iam.ServicePrincipal("apigateway.amazonaws.com"),
            role_name=role_name,
        )
        api_role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=["*"],
            )
        )
        cfn_role: iam.CfnRole = api_role.node.default_child  # type: ignore
        cfn_role.override_logical_id(role_name)

        wellapi_app: WellApi = import_app(self.app_srt, self.handlers_dir)

        self._create_api(wellapi_app, cache_enable=cache_enable, log_enable=log_enable, cors=cors, role_name=role_name)

        for q in wellapi_app.queues:
            queue = sqs.Queue(self, f"{q.queue_name}Queue", queue_name=q.queue_name)

        shared_layer_asset = s3_assets.Asset(
            self,
            "SharedLayerAsset",
            path=".",
            bundling=core.BundlingOptions(
                local=PackageBundling(  # type: ignore
                    target="dep",
                    zip_name=DEP_LAYOUT_FILE,
                ),
                image=core.DockerImage.from_registry("ghcr.io/astral-sh/uv:python3.12-alpine"),
                command=[
                    "sh",
                    "-c",
                    "uv sync --all-groups"
                    f"wellapi build dep {DEP_LAYOUT_FILE}",
                    f"cp {DEP_LAYOUT_FILE} /asset-output/"
                ],
                output_type=core.BundlingOutput.ARCHIVED,
            )
        )

        shared_layer = [
            _lambda.LayerVersion(
                self,
                "SharedLayer",
                code=_lambda.Code.from_bucket(
                    bucket=shared_layer_asset.bucket,
                    key=shared_layer_asset.s3_object_key,
                ),
                compatible_runtimes=[_lambda.Runtime.PYTHON_3_12],  # type: ignore
                layer_version_name="shared_layer",
            )
        ]

        code_asset = s3_assets.Asset(
            self,
            "CodeAsset",
            path=".",
            bundling=core.BundlingOptions(
                local=PackageBundling(  # type: ignore
                    target="app",
                    zip_name=APP_LAYOUT_FILE,
                ),
                image=core.DockerImage.from_registry(
                    "ghcr.io/astral-sh/uv:python3.12-alpine"),
                command=[
                    "sh",
                    "-c",
                    "uv sync --all-groups"
                    f"wellapi build app {APP_LAYOUT_FILE}",
                    f"cp {APP_LAYOUT_FILE} /asset-output/"
                ],
                output_type=core.BundlingOutput.ARCHIVED,
            )
        )
        code_layer = _lambda.Code.from_bucket(
            bucket=code_asset.bucket,
            key=code_asset.s3_object_key,
        )

        self.lambda_role = iam.Role(
            self,
            "LambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole"),
            ],
        )

        lmbd: Lambda
        for lmbd in wellapi_app.lambdas:
            lambda_function = _lambda.Function(
                self,
                f"{lmbd.arn}Function",
                function_name=f"{lmbd.arn}Function",
                runtime=_lambda.Runtime.PYTHON_3_12,  # type: ignore
                handler=lmbd.unique_id,
                memory_size=lmbd.memory_size,
                timeout=Duration.seconds(lmbd.timeout),
                code=code_layer,
                layers=shared_layer,  # type: ignore
                vpc=vpc,
                vpc_subnets=vpc_subnets,
                security_groups=sg,
                environment=environment,
                role=self.lambda_role
            )

            if lmbd.type_ == "endpoint":
                cfn_lambda: _lambda.CfnFunction = lambda_function.node.default_child  # type: ignore
                cfn_lambda.override_logical_id(f"{lmbd.arn}Function")

            if lmbd.type_ == "queue":
                queue = sqs.Queue(
                    self,
                    f"{lmbd.name}Queue",
                    queue_name=lmbd.path,
                    visibility_timeout=Duration.seconds(lmbd.timeout),
                )

                sqs_event_source = lambda_event_source.SqsEventSource(queue)  # type: ignore

                # Add SQS event source to the Lambda function
                lambda_function.add_event_source(sqs_event_source)

            if lmbd.type_ == "job":
                rule = events.Rule(
                    self,
                    f"{lmbd.name}Rule",
                    schedule=events.Schedule.expression(lmbd.path),
                )

                rule.add_target(targets.LambdaFunction(lambda_function))  # type: ignore

    def _create_api(
        self,
        wellapi_app: WellApi,
        cors: bool,
        role_name: str,
        cache_enable: bool = False,
        log_enable: bool = False
    ) -> None:
        # defining a Cfn Asset from the openAPI file
        open_api_asset = s3_assets.Asset(
            self,
            "OpenApiAsset",
            path=".",
            bundling=core.BundlingOptions(
                local=OpenAPIBundling(  # type: ignore
                    app_srt=self.app_srt,
                    handlers_dir=self.handlers_dir,
                    cors=cors,
                    role_name=role_name,
                    openapi_file=OPENAPI_FILE,
                ),
                image=core.DockerImage.from_registry("ghcr.io/astral-sh/uv:python3.12-alpine"),
                command=[
                    "sh",
                    "-c",
                    "uv sync --all-groups"
                    f"wellapi openapi {self.app_srt} {self.handlers_dir} --output {OPENAPI_FILE} --cors {cors} --role_name {role_name}",
                    f"cp {OPENAPI_FILE} /asset-output/"
                ],
                output_type=core.BundlingOutput.SINGLE_FILE,
            )
        )
        transform_map = {"Location": open_api_asset.s3_object_url}
        data = Fn.transform("AWS::Include", transform_map)

        cache_deploy_options = {}
        if cache_enable:
            cache_deploy_options = {
                "cache_cluster_enabled": cache_enable,
                # "cache_cluster_size": "0.5",
                # "cache_ttl": Duration.minutes(15),
                "method_options": {
                    # "{resource_path}/{http_method}": apigw.MethodDeploymentOptions
                    "/*/*": apigw.MethodDeploymentOptions(
                        caching_enabled=True,
                        cache_ttl=Duration.minutes(15),
                    )
                },
            }

        log_deploy_options = {}
        if log_enable:
            access_log_group = logs.LogGroup(
                self,
                "AccessLogGroup",
                log_group_name="AccessLogGroup-apiGateway",
                retention=logs.RetentionDays.ONE_MONTH,
            )
            log_deploy_options = {
                "logging_level": apigw.MethodLoggingLevel.ERROR,
                # data_trace_enabled=True,
                "metrics_enabled": True,
                "access_log_destination": apigw.LogGroupLogDestination(
                    access_log_group  # type: ignore
                ),
                "access_log_format": apigw.AccessLogFormat.custom(
                    json.dumps(
                        {
                            "request_id": apigw.AccessLogField.context_request_id(),
                            "source_ip": apigw.AccessLogField.context_identity_source_ip(),
                            "method": apigw.AccessLogField.context_http_method(),
                            "path": apigw.AccessLogField.context_resource_path(),
                            "request_path": apigw.AccessLogField.context_path(),
                            "status": apigw.AccessLogField.context_status(),
                            "user_agent": apigw.AccessLogField.context_identity_user_agent(),
                            "integration_id": apigw.AccessLogField.context_aws_endpoint_request_id(),
                        }
                    )
                ),
            }

        if cache_deploy_options or log_deploy_options:
            deploy_options = apigw.StageOptions(
                **cache_deploy_options, **log_deploy_options
            )
        else:
            deploy_options = None

        self.api = apigw.SpecRestApi(
            self,
            f"{wellapi_app.title}Api",
            api_definition=apigw.ApiDefinition.from_inline(data),
            deploy_options=deploy_options,
        )

        self.api_key = apigw.ApiKey(
            self,
            "MyApiKey",
            api_key_name="my-service-key",
        )

        self.usage_plan = apigw.UsagePlan(
            self,
            "MyUsagePlan",
            api_stages=[
                apigw.UsagePlanPerApiStage(
                    api=self.api,
                    stage=self.api.deployment_stage,
                )
            ],
            name="MyUsagePlan",
            quota=apigw.QuotaSettings(
                limit=10_000,
                period=apigw.Period.DAY,
            ),
            throttle=apigw.ThrottleSettings(
                burst_limit=2,
                rate_limit=10,
            ),
        )
        self.usage_plan.add_api_key(self.api_key)
