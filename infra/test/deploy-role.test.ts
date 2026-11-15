import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { App } from 'aws-cdk-lib'
import { Match, Template } from 'aws-cdk-lib/assertions'
import { build } from '../bin/app.js'
import { DeployRoleStack } from '../lib/deploy-role-stack.js'

function synth() {
  const { app, deploy } = build(new App())
  app.synth()
  return { deploy, template: Template.fromStack(deploy) }
}

test('only the repository, in the production environment, can assume the role', () => {
  const { template } = synth()
  template.hasResourceProperties('AWS::IAM::Role', {
    MaxSessionDuration: 3600,
    AssumeRolePolicyDocument: {
      Statement: [
        Match.objectLike({
          Action: 'sts:AssumeRoleWithWebIdentity',
          Effect: 'Allow',
          Condition: {
            StringEquals: {
              'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
              'token.actions.githubusercontent.com:sub': 'repo:Gunjanmishra481/laabam-paytm:environment:production',
            },
          },
        }),
      ],
    },
  })
  // no StringLike: a wildcard sub would let any branch, PR or environment in
  const json = JSON.stringify(template.toJSON())
  assert.ok(!json.includes('StringLike'))
})

test('the OIDC provider trusts GitHub for STS only', () => {
  const { template } = synth()
  template.resourceCountIs('AWS::IAM::OIDCProvider', 1)
  template.hasResourceProperties('AWS::IAM::OIDCProvider', {
    Url: 'https://token.actions.githubusercontent.com',
    ClientIdList: ['sts.amazonaws.com'],
  })
})

test('the role can only assume the CDK bootstrap roles', () => {
  const { template } = synth()
  const policies = template.findResources('AWS::IAM::Policy')
  const statements = Object.values(policies).flatMap((p) => p.Properties.PolicyDocument.Statement)
  assert.equal(statements.length, 1)
  assert.equal(statements[0].Action, 'sts:AssumeRole')
  assert.equal(statements[0].Effect, 'Allow')
  const resources = JSON.stringify(statements[0].Resource)
  for (const kind of ['deploy', 'file-publishing', 'image-publishing', 'lookup']) {
    assert.ok(resources.includes(`:role/cdk-hnb659fds-${kind}-role-`), kind)
  }
  template.resourcePropertiesCountIs('AWS::IAM::Role', { ManagedPolicyArns: Match.anyValue() }, 0)
})

test('an existing OIDC provider is reused, not created again', () => {
  const stack = new DeployRoleStack(new App(), 'Reuse', {
    repository: 'o/r',
    environment: 'production',
    existingProviderArn: 'arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com',
  })
  Template.fromStack(stack).resourceCountIs('AWS::IAM::OIDCProvider', 0)
})

test('bad repository or environment names are refused', () => {
  assert.throws(() => new DeployRoleStack(new App(), 'A', { repository: 'nope', environment: 'production' }))
  assert.throws(() => new DeployRoleStack(new App(), 'B', { repository: 'o/r', environment: 'prod*' }))
})

test('cdk-nag (AwsSolutions): synth passes; without the four acknowledgements it fails on exactly those', () => {
  assert.doesNotThrow(() => build(new App()).app.synth())
  const outdir = mkdtempSync(join(tmpdir(), 'laabam-nag-'))
  assert.throws(() => build(new App({ outdir }), false).app.synth(), /Validation failed/)
  const report = readFileSync(join(outdir, 'validation-report.json'), 'utf8')
  const ids = [...new Set(report.match(/AwsSolutions-[A-Z]+\d+(\[[^\]"]*\])?/g) ?? [])]
  const iam5 = ids.filter((id) => id.startsWith('AwsSolutions-IAM5['))
  assert.equal(iam5.length, 4, ids.join(', '))
  assert.ok(ids.every((id) => id.startsWith('AwsSolutions-IAM5')), ids.join(', '))
})
