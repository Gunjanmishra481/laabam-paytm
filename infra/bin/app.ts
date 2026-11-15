// CDK app. `npm run synth` writes CloudFormation to cdk.out/ without calling AWS. Nothing here is deployed by CI.
// cdk-nag (AwsSolutions pack) runs as a CDK validation plugin: an unacknowledged finding fails the synth.
import { App, Validations } from 'aws-cdk-lib'
import { AwsSolutionsChecks } from 'cdk-nag'
import { DeployRoleStack } from '../lib/deploy-role-stack.js'

export function build(app = new App(), acknowledge = true): { app: App; deploy: DeployRoleStack } {
  const deploy = new DeployRoleStack(app, 'LaabamGitHubDeployRole', {
    repository: 'Gunjanmishra481/laabam-paytm',
    environment: 'production',
    description: 'Laabam: the role GitHub Actions assumes to deploy (OIDC, no stored keys)',
  })
  if (acknowledge) {
    // The CDK bootstrap role names end in the region (cdk-<qualifier>-<kind>-role-<account>-<region>): the
    // wildcard covers only that suffix, for sts:AssumeRole on those four roles in this account.
    for (const kind of ['deploy', 'file-publishing', 'image-publishing', 'lookup']) {
      Validations.of(deploy.role).acknowledge({
        id: `AwsSolutions::AwsSolutions-IAM5[Resource::arn:<AWS::Partition>:iam::<AWS::AccountId>:role/cdk-hnb659fds-${kind}-role-<AWS::AccountId>-*]`,
        reason: 'Only the region suffix of a CDK bootstrap role name is a wildcard; sts:AssumeRole on that role only.',
      })
    }
  }
  Validations.of(app).addPlugins(new AwsSolutionsChecks(app, { verbose: true }))
  return { app, deploy }
}

if (process.argv[1]?.endsWith('app.js')) build().app.synth()
