"""Stack genérico: Lambda con FastAPI (Mangum) detrás de API Gateway, secretos en Secrets Manager,
front estático en S3 y CloudFront opcional. Todo se configura en config.json, no hace falta tocar este archivo."""

import json
from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

INFRA = Path(__file__).resolve().parent
CODIGO = INFRA.parent / "dist" / "lambda"


class ServicioStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, config: dict, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Los secretos nacen vacíos, el valor se carga después del deploy (scripts/cargar_secretos.sh),
        # así nunca quedan en el template ni en variables de entorno
        secretos = {
            variable: secretsmanager.Secret(self, f"Secreto{variable}", secret_name=nombre, removal_policy=RemovalPolicy.DESTROY)
            for variable, nombre in config.get("secretos", {}).items()
        }

        # Tablas {"VARIABLE_DE_ENTORNO": "clave_de_particion"}, on-demand, la Lambda recibe el nombre por la variable
        tablas = {
            variable: dynamodb.Table(
                self,
                f"Tabla{variable}",
                partition_key=dynamodb.Attribute(name=clave, type=dynamodb.AttributeType.STRING),
                billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
                removal_policy=RemovalPolicy.DESTROY,
            )
            for variable, clave in config.get("tablas", {}).items()
        }

        funcion = lambda_.Function(
            self,
            "Fn",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="app.lambda_handler.handler",
            code=lambda_.Code.from_asset(str(CODIGO)),
            memory_size=config.get("memoria_mb", 512),
            timeout=Duration.seconds(config.get("timeout_s", 29)),
            environment={
                **config.get("env", {}),
                **{v: t.table_name for v, t in tablas.items()},
                "SECRETS": json.dumps({v: s.secret_arn for v, s in secretos.items()}),
            },
            log_group=logs.LogGroup(self, "Logs", retention=logs.RetentionDays.TWO_WEEKS, removal_policy=RemovalPolicy.DESTROY),
        )

        # Mínimo privilegio, leer solo sus secretos e invocar modelos de Bedrock
        for secreto in secretos.values():
            secreto.grant_read(funcion)
        for tabla in tablas.values():
            tabla.grant_read_write_data(funcion)
        funcion.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=["arn:aws:bedrock:*::foundation-model/*", f"arn:aws:bedrock:*:{self.account}:inference-profile/*"],
        ))

        # Proxy, todas las rutas las resuelve FastAPI. El preflight CORS lo responde API Gateway.
        rps = config.get("throttle_rps", 5)
        api = apigw.LambdaRestApi(
            self,
            "Api",
            handler=funcion,
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS, allow_methods=apigw.Cors.ALL_METHODS, allow_headers=["content-type"]
            ),
            deploy_options=apigw.StageOptions(stage_name="dev", throttling_rate_limit=rps, throttling_burst_limit=rps * 2),
        )
        # Errores de API Gateway con CORS, si no el navegador solo muestra un error de CORS
        for tipo in (apigw.ResponseType.DEFAULT_4_XX, apigw.ResponseType.DEFAULT_5_XX):
            api.add_gateway_response(f"Cors{tipo.response_type}", type=tipo, response_headers={"Access-Control-Allow-Origin": "'*'"})

        CfnOutput(self, "ApiUrl", value=api.url)
        for variable, tabla in tablas.items():
            CfnOutput(self, f"Tabla{variable}Nombre", value=tabla.table_name)
        for variable, secreto in secretos.items():
            CfnOutput(self, f"Secreto{variable}Nombre", value=secreto.secret_name)

        if config.get("frontend"):
            bucket = s3.Bucket(
                self,
                "Frontend",
                website_index_document="index.html",
                public_read_access=True,
                block_public_access=s3.BlockPublicAccess(
                    block_public_acls=True, ignore_public_acls=True, block_public_policy=False, restrict_public_buckets=False
                ),
                removal_policy=RemovalPolicy.DESTROY,
                auto_delete_objects=True,
            )
            s3deploy.BucketDeployment(
                self,
                "FrontendDeploy",
                destination_bucket=bucket,
                sources=[
                    s3deploy.Source.asset(str((INFRA / config["frontend"]).resolve())),
                    # La URL base de la API se resuelve en el deploy
                    s3deploy.Source.json_data("config.json", {"api_base": api.url.rstrip("/")}),
                ],
            )
            CfnOutput(self, "FrontendUrl", value=bucket.bucket_website_url)

        # CloudFront es opcional porque las cuentas nuevas necesitan verificación de AWS Support (make deploy-cdn)
        if self.node.try_get_context("cdn") in ("true", True):
            cdn = cloudfront.Distribution(
                self,
                "Cdn",
                default_behavior=cloudfront.BehaviorOptions(
                    origin=origins.RestApiOrigin(api),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                ),
                price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            )
            CfnOutput(self, "CdnUrl", value=f"https://{cdn.distribution_domain_name}/")
