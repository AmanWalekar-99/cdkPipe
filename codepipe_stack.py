from aws_cdk import (
    aws_codebuild as codebuild,
    aws_codecommit as codecommit,
    aws_codepipeline as pipeline,
    aws_codepipeline_actions as pipelineactions,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_s3 as s3,
    CfnOutput,
    Stack,
)
from constructs import Construct
import os.path

dirname = os.path.dirname(__file__)
class CodepipeStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

         # Creates an AWS CodeCommit repository
        code_repo = codecommit.Repository(
            self, "CodeRepo",
            repository_name="simple-app-code-repo",
            # Copies files from app directory to the repo as the initial commit
            code=codecommit.Code.from_directory("app", "main")
        )

        # Creates an S3 bucket to store the build artifacts
        artifact_bucket = s3.Bucket(self, "ArtifactBucket")

        # CodeBuild project that builds the Python app
        build_project = codebuild.PipelineProject(
            self, "BuildProject",
            project_name="PythonAppBuildProject",
            build_spec=codebuild.BuildSpec.from_object({
                "version": "0.2",
                "phases": {
                    "install": {
                        "runtime-versions": {
                            "python": "3.9"
                        },
                        "commands": [
                            "pip install -r requirements.txt"
                        ]
                    },
                    "build": {
                        "commands": [
                            "echo Building the Python app...",
                            "python build.py"
                        ]
                    }
                },
                "artifacts": {
                    "files": [
                        "**/*"
                    ],
                    "base-directory": "dist"
                }
            }),
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_5_0
            ),
            environment_variables={
                "BUCKET_NAME": codebuild.BuildEnvironmentVariable(value=artifact_bucket.bucket_name)
            }
        )

        # Grants CodeBuild project access to the CodeCommit repository
        code_repo.grant_read(build_project)

        # Lambda function that triggers CodeBuild project
        # trigger_code_build = lambda_.Function(
        #     self, "BuildLambda",
        #     runtime=lambda_.Runtime.PYTHON_3_9,
        #     code=lambda_.Code.from_asset("lambda"),
        #     handler="trigger_build.handler",
        #     environment={
        #         "PROJECT_NAME": build_project.project_name
        #     },
        #     initial_policy=[
        #         iam.PolicyStatement(
        #             effect=iam.Effect.ALLOW,
        #             actions=["codebuild:StartBuild"],
        #             resources=[build_project.project_arn]
        #         )
        #     ]
        # )

        # # Triggers a Lambda function using AWS SDK
        # trigger_lambda = pipelineactions.LambdaInvokeAction(
        #     action_name="TriggerCodeBuild",
        #     lambda_=trigger_code_build,
        #     user_parameters={
        #         "ProjectName": build_project.project_name
        #     }
        # )

        # Creates a VPC for the EC2 instance
        vpc = ec2.Vpc(
            self, "InstanceVpc",
            cidr="10.0.0.0/16"
        )

        # Creates a security group for the EC2 instance
        instance_sg = ec2.SecurityGroup(
            self, "InstanceSecurityGroup",
            vpc=vpc,
            allow_all_outbound=True
        )

        # Allows inbound SSH access to the EC2 instance
        instance_sg.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(22),
            description="Allow SSH access",
            remote_rule=False
        )

        # Creates an EC2 instance
        instance = ec2.Instance(
            self, "AppInstance",
            instance_type=ec2.InstanceType("t2.micro"),
            machine_image=ec2.AmazonLinuxImage(generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2),
            vpc=vpc,
            security_group=instance_sg,
            user_data=ec2.UserData.for_linux(shebang="#!/bin/bash")
        )

        instance.user_data.add_commands(
            "yum install -y python3",
            "aws s3 cp s3://" + artifact_bucket.bucket_name + "/dist/djangopro.tar.gz /tmp/djangopro.tar.gz",
            "tar -zxvf /tmp/djangopro.tar.gz -C /tmp",
            "cd /tmp/bloodbankmanagement",
            "python3 manage.py runserver 0.0.0.0:8000"
)

        # Grants the EC2 instance read access to the S3 artifact bucket
        artifact_bucket.grant_read(instance)

        # Creates the pipeline
        code_pipeline = pipeline.Pipeline(
            self, "BuildDeployPipeline",
            pipeline_name="PythonAppPipeline",
            stages=[
                pipeline.StageProps(
                    stage_name="Source",
                    actions=[
                        pipelineactions.CodeCommitSourceAction(
                            action_name="CodeCommit",
                            branch="main",
                            repository=code_repo,
                            output=pipeline.Artifact("SourceOutput")
                        )
                    ]
                ),
                pipeline.StageProps(
                    stage_name="Build",
                    actions=[
                        pipelineactions.CodeBuildAction(
                            action_name="Build",
                            project=build_project,
                            input=pipeline.Artifact("SourceOutput"),
                            outputs=[pipeline.Artifact("BuildOutput")]
                        ),
                        # trigger_lambda
                    ]
                ),
                pipeline.StageProps(
                    stage_name="Deploy",
                    actions=[
                        pipelineactions.S3DeployAction(
                            action_name="Deploy",
                            input=pipeline.Artifact("BuildOutput"),
                            bucket=artifact_bucket,
                            extract=True
                        ),
                        pipelineactions.CloudFormationCreateUpdateStackAction(
                            action_name="UpdateStack",
                            template_path=pipeline.ArtifactPath(pipeline.Artifact("BuildOutput"), "template.yaml"),
                            stack_name="PythonAppStack",
                            admin_permissions=True
                        )
                    ]
                )
            ]
        )

        # Outputs the EC2 instance public IP
        CfnOutput(
            self, "InstancePublicIp",
            value=instance.instance_public_ip
        )