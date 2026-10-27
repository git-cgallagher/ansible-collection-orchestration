# Security policy

## Reporting a vulnerability

Please report security issues **privately** through GitHub:
[Report a vulnerability](https://github.com/git-cgallagher/ansible-collection-orchestration/security/advisories/new)
(the **Security** tab, then **Report a vulnerability**). Don't open a public issue for a security problem.

You can expect an acknowledgement within a week. This is a one-person project, so fixes are best effort.

## Supported versions

Only the latest release gets fixes.

## Scope

This collection holds an Ansible strategy plugin. It runs on the Ansible controller, inside
`ansible-playbook`, with the same privileges as the rest of your playbook run. It has no network
listeners, stores no credentials and makes no outbound connections of its own.
