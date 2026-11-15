// The role GitHub Actions assumes to deploy Laabam (security plan S4). Not deployed yet: S5 does that, by hand,
// after review. No long-lived AWS keys anywhere: GitHub's OIDC token is exchanged for 1-hour credentials.
//
// Who can assume it: only workflows in one repository, running in one GitHub environment ("production", which has
// required reviewers and allows only the main branch: docs/github-settings.md). A pull request, a fork or another
// branch gets a different `sub` claim and is refused.
//
// What it can do: assume the CDK bootstrap roles in this account (deploy, file and image publishing, lookups),
// nothing else. Those roles do the actual deployment through CloudFormation, so the GitHub role itself can't
// touch data, read secrets or change IAM.
import { CfnOutput, Duration, Stack, type StackProps } from 'aws-cdk-lib'
import * as iam from 'aws-cdk-lib/aws-iam'
import type { Construct } from 'constructs'

export const GITHUB_OIDC_URL = 'https://token.actions.githubusercontent.com'
export const GITHUB_OIDC_HOST = 'token.actions.githubusercontent.com'

export interface DeployRoleProps extends StackProps {
  /** owner/name of the GitHub repository allowed to deploy */
  readonly repository: string
  /** the GitHub environment deploy jobs run in */
  readonly environment: string
  /** CDK bootstrap qualifier (the default is hnb659fds) */
  readonly cdkQualifier?: string
  /** an account has one GitHub OIDC provider; pass its ARN if it already exists */
  readonly existingProviderArn?: string
}

export class DeployRoleStack extends Stack {
  readonly role: iam.Role

  constructor(scope: Construct, id: string, props: DeployRoleProps) {
    super(scope, id, props)
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(props.repository)) throw new Error('repository must be owner/name')
    if (!/^[A-Za-z0-9_.-]+$/.test(props.environment)) throw new Error('environment must be a plain name')
    const qualifier = props.cdkQualifier ?? 'hnb659fds'

    const providerArn =
      props.existingProviderArn ??
      new iam.CfnOIDCProvider(this, 'GitHubOidc', {
        url: GITHUB_OIDC_URL,
        clientIdList: ['sts.amazonaws.com'],
      }).attrArn

    this.role = new iam.Role(this, 'GitHubDeployRole', {
      roleName: 'laabam-github-deploy',
      description: `GitHub Actions deploys from ${props.repository} (environment ${props.environment}) only`,
      maxSessionDuration: Duration.hours(1),
      assumedBy: new iam.WebIdentityPrincipal(providerArn, {
        StringEquals: {
          [`${GITHUB_OIDC_HOST}:aud`]: 'sts.amazonaws.com',
          [`${GITHUB_OIDC_HOST}:sub`]: `repo:${props.repository}:environment:${props.environment}`,
        },
      }),
    })

    this.role.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AssumeCdkBootstrapRolesOnly',
        actions: ['sts:AssumeRole'],
        resources: ['deploy', 'file-publishing', 'image-publishing', 'lookup'].map(
          (kind) => `arn:${this.partition}:iam::${this.account}:role/cdk-${qualifier}-${kind}-role-${this.account}-*`,
        ),
      }),
    )

    new CfnOutput(this, 'DeployRoleArn', {
      value: this.role.roleArn,
      description: 'Set as the AWS_DEPLOY_ROLE_ARN variable of the GitHub environment',
    })
  }
}
