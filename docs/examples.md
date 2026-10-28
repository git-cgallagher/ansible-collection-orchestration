# time_offset examples

Practical examples for `git_cgallagher.orchestration.time_offset`, from the basics to real-world patterns.
Start with the [README](../README.md) for what the strategy does. All the settings are ordinary variables:

| Variable | Default | Meaning |
|---|---|---|
| `time_offset_seconds` | `0` | Minimum gap between two starts in the same queue |
| `time_offset_jitter` | `0` | Extra random delay, 0 up to this many seconds, added to each gap |
| `time_offset_key` | the task | Tasks with the same key share one queue |
| `time_offset_check_when` | `true` | Evaluate `when:` before holding a task, so skipped tasks take no slot |

Every example uses `strategy: git_cgallagher.orchestration.time_offset`. Without any of the variables
above, a play behaves exactly like the `free` strategy.

**Contents**

- [The basics](#the-basics): 1–8
- [Spacing everything that hits one target](#spacing-everything-that-hits-one-target): 9–12
- [Combining with other Ansible features](#combining-with-other-ansible-features): 13–19
- [Real-world recipes](#real-world-recipes): 20–28
- [When not to use it](#when-not-to-use-it)
- [Seeing what it's doing](#seeing-what-its-doing)

---

## The basics

### 1. Offset every task in a play

Every task in the play starts at least 2 seconds apart across hosts. Tasks still overlap; only their starts
are spaced.

```yaml
- name: Configure web servers
  hosts: web
  strategy: git_cgallagher.orchestration.time_offset
  vars:
    time_offset_seconds: 2
  tasks:
    - name: Install nginx
      ansible.builtin.package:
        name: nginx
        state: present

    - name: Deploy the site config
      ansible.builtin.template:
        src: site.conf.j2
        dest: /etc/nginx/conf.d/site.conf
        mode: "0644"
```

With 20 hosts, the first task's starts spread over about 38 s. After that, the hosts are already spread
out, so later tasks are rarely held back.

### 2. Offset only one task

Everything runs like `free` except the one task that causes trouble when hosts hit it at once.

```yaml
- name: Register hosts with the inventory service
  hosts: all
  strategy: git_cgallagher.orchestration.time_offset
  tasks:
    - name: Gather facts as usual (not offset)
      ansible.builtin.setup:

    - name: Register with the CMDB
      ansible.builtin.uri:
        url: "https://cmdb.example.com/api/hosts/{{ inventory_hostname }}"
        method: PUT
        body_format: json
        body:
          os: "{{ ansible_facts['distribution'] }}"
      vars:
        time_offset_seconds: 1
```

### 3. Offset a block of tasks

Variables on a block apply to every task inside it.

```yaml
- name: Bootstrap new Debian hosts
  hosts: new_hosts
  strategy: git_cgallagher.orchestration.time_offset
  tasks:
    - name: Steps that call the package mirror
      vars:
        time_offset_seconds: 3
      block:
        - name: Refresh package metadata
          ansible.builtin.apt:
            update_cache: true

        - name: Install the base packages
          ansible.builtin.apt:
            name: [curl, chrony, rsync]
            state: present

    - name: Not offset
      ansible.builtin.service:
        name: chronyd
        state: started
        enabled: true
```

### 4. Add jitter so hosts don't fall into a rhythm

The gap is the minimum. Jitter adds a random 0 to N seconds on top, so a shared backend doesn't see starts
arriving like clockwork.

```yaml
- name: Pull container images
  hosts: docker_hosts
  strategy: git_cgallagher.orchestration.time_offset
  vars:
    time_offset_seconds: 2
    time_offset_jitter: 3          # each gap is 2-5 s
  tasks:
    - name: Pull the application image
      community.docker.docker_image:
        name: registry.example.com/app:1.4.2
        source: pull
```

### 5. Fractional gaps

Gaps can be fractions of a second, for example to stay under an API's per-second rate limit.

```yaml
- name: Update DNS records through the provider API
  hosts: all
  strategy: git_cgallagher.orchestration.time_offset
  tasks:
    - name: Create the A record
      ansible.builtin.uri:
        url: "https://dns.example.com/api/records"
        method: POST
        body_format: json
        body:
          name: "{{ inventory_hostname }}"
          type: A
          value: "{{ ansible_host }}"
        status_code: [200, 201]
      delegate_to: localhost
      vars:
        time_offset_seconds: 0.25    # at most about 4 starts per second
```

### 6. Spread starts over a fixed window, whatever the host count

Because the gap is templated per host, it can be computed. This spreads the first starts over about
60 s, however many hosts the play has.

```yaml
- name: Restart agents across the fleet
  hosts: all
  strategy: git_cgallagher.orchestration.time_offset
  vars:
    time_offset_seconds: "{{ 60 / (ansible_play_hosts_all | length) }}"
  tasks:
    - name: Restart the monitoring agent
      ansible.builtin.service:
        name: node_exporter
        state: restarted
```

### 7. Different gaps per environment

The settings are variables, so the usual variable precedence applies. Put them in `group_vars`, and the
playbook stays free of timing details.

```yaml
# group_vars/production.yml
time_offset_seconds: 5
time_offset_jitter: 2
```

```yaml
# group_vars/staging.yml
time_offset_seconds: 0             # staging: no spacing, run like free
```

```yaml
# site.yml
- name: Deploy the application
  hosts: app
  strategy: git_cgallagher.orchestration.time_offset
  tasks:
    - name: Restart the app service
      ansible.builtin.service:
        name: app
        state: restarted
```

### 8. Make it the default strategy

In `ansible.cfg`, every play uses it unless it names another strategy. With no offset variables set,
that's identical to `free`.

```ini
# ansible.cfg
[defaults]
strategy = git_cgallagher.orchestration.time_offset
```

Or for one run, through the environment:

```bash
ANSIBLE_STRATEGY=git_cgallagher.orchestration.time_offset ansible-playbook site.yml
```

---

## Spacing everything that hits one target

By default each task has its own queue of start slots, so two *different* tasks can still start at the
same moment. `time_offset_key` puts tasks into one shared queue.

### 9. Several tasks that all call the same management server

Host 1 might run "Find closest domain controller" at the moment host 2 runs "Pre-stage the computer
account". Both go through `mgmt01`. A shared key spaces all of them against each other.

```yaml
- name: Join Windows servers to the domain
  hosts: windows
  strategy: git_cgallagher.orchestration.time_offset
  tasks:
    - name: Everything that goes through the management server
      delegate_to: "{{ mgmt_server }}"
      vars:
        time_offset_seconds: 3
        time_offset_key: "{{ mgmt_server }}"
      block:
        - name: Find closest domain controller
          ansible.windows.win_shell: nltest /dsgetdc:{{ ad_domain }}
          register: dc_lookup

        - name: Pre-stage the computer account
          microsoft.ad.computer:
            name: "{{ inventory_hostname_short }}"
            path: "OU=Servers,DC=example,DC=com"
```

### 10. One queue per target, chosen per host

The key is templated per host. When different hosts use different servers, each server gets its own
queue, and hosts going to `db-east` never wait for hosts going to `db-west`.

```yaml
- name: Create application database users
  hosts: app
  strategy: git_cgallagher.orchestration.time_offset
  tasks:
    - name: Create the user on this host's database primary
      community.postgresql.postgresql_user:
        name: "app_{{ inventory_hostname_short }}"
        login_host: "{{ db_primary }}"      # set per host in inventory
      delegate_to: localhost
      vars:
        time_offset_seconds: 1
        time_offset_key: "{{ db_primary }}"
```

### 11. A load balancer shared by drain and re-enable steps

Drain and re-enable are different tasks against the same load balancer. One key keeps them from piling
onto its admin socket at once.

```yaml
- name: Rolling restart behind HAProxy
  hosts: web
  strategy: git_cgallagher.orchestration.time_offset
  vars:
    lb_key: "haproxy-{{ lb_host }}"
  tasks:
    - name: Drain from the load balancer
      community.general.haproxy:
        backend: web
        host: "{{ inventory_hostname }}"
        state: disabled
      delegate_to: "{{ lb_host }}"
      vars:
        time_offset_seconds: 2
        time_offset_key: "{{ lb_key }}"

    - name: Restart the app
      ansible.builtin.service:
        name: app
        state: restarted

    - name: Put back into the load balancer
      community.general.haproxy:
        backend: web
        host: "{{ inventory_hostname }}"
        state: enabled
      delegate_to: "{{ lb_host }}"
      vars:
        time_offset_seconds: 2
        time_offset_key: "{{ lb_key }}"
```

### 12. A key shared across plays

Queues last for the whole `ansible-playbook` run, so two plays using the same key share one queue. That's
useful when separate plays hit the same backend.

```yaml
- name: Provision VMs
  hosts: new_vms
  strategy: git_cgallagher.orchestration.time_offset
  vars:
    time_offset_seconds: 5
    time_offset_key: vcenter
  tasks:
    - name: Clone from template
      community.vmware.vmware_guest:
        name: "{{ inventory_hostname }}"
        template: rhel9-template
        datacenter: DC1
        folder: /DC1/vm/apps
        state: poweredon
      delegate_to: localhost

- name: Tag VMs
  hosts: new_vms
  strategy: git_cgallagher.orchestration.time_offset
  vars:
    time_offset_seconds: 5
    time_offset_key: vcenter          # same queue as the play above
  tasks:
    - name: Apply the backup tag
      community.vmware.vmware_tag_manager:
        object_name: "{{ inventory_hostname }}"
        object_type: VirtualMachine
        tag_names: ["backup:daily"]
        state: add
      delegate_to: localhost
```

Within one key, keep a single gap. Each task applies its own `time_offset_seconds` against the previous
start in the queue, so a 1 s task right after a 5 s task waits only 1 s.

---

## Combining with other Ansible features

### 13. `throttle`: space the starts *and* cap how many run

The gap spaces when tasks start; it doesn't limit how many are running. For a long task, add `throttle`.

```yaml
- name: Clone VMs from a template
  hosts: new_vms
  gather_facts: false
  strategy: git_cgallagher.orchestration.time_offset
  tasks:
    - name: Clone and customize
      community.vmware.vmware_guest:
        name: "{{ inventory_hostname }}"
        template: win2022-template
        datacenter: DC1
        folder: /DC1/vm/windows
        state: poweredon
        customization:
          hostname: "{{ inventory_hostname_short }}"
        wait_for_customization: true
      delegate_to: localhost
      throttle: 4                    # at most 4 clones running at once...
      vars:
        time_offset_seconds: 5       # ...and no two starting within 5 s of each other
```

### 14. `serial`: rolling batches

The gap also holds across batch boundaries: the first host of batch 2 starts at least a gap after the
last host of batch 1.

```yaml
- name: Rolling OS updates, 25% at a time
  hosts: app
  serial: "25%"
  strategy: git_cgallagher.orchestration.time_offset
  vars:
    time_offset_seconds: 10
  tasks:
    - name: Apply updates
      ansible.builtin.package:
        name: "*"
        state: latest

    - name: Reboot
      ansible.builtin.reboot:
        reboot_timeout: 900
```

### 15. Handlers

Handlers are spaced like any other task. Here, service restarts triggered by a config change don't all
re-register with service discovery at the same moment.

```yaml
- name: Update the service config
  hosts: app
  strategy: git_cgallagher.orchestration.time_offset
  tasks:
    - name: Deploy config
      ansible.builtin.template:
        src: app.conf.j2
        dest: /etc/app/app.conf
        mode: "0644"
      notify: Restart app

  handlers:
    - name: Restart app
      ansible.builtin.service:
        name: app
        state: restarted
      vars:
        time_offset_seconds: 3
```

### 16. Included task files

Set the variables inside the included file, on the tasks that need them. Variables on the
`include_tasks` line also work, but they space the include step itself too, which takes an extra slot
per host in a shared queue.

```yaml
# site.yml
- name: Join the domain
  hosts: windows
  strategy: git_cgallagher.orchestration.time_offset
  tasks:
    - name: Domain join steps
      ansible.builtin.include_tasks: domain_join.yml
```

```yaml
# domain_join.yml
- name: Steps that go through the management server
  vars:
    time_offset_seconds: 3
    time_offset_key: "{{ mgmt_server }}"
  block:
    - name: Find closest domain controller
      ansible.windows.win_shell: nltest /dsgetdc:{{ ad_domain }}
      delegate_to: "{{ mgmt_server }}"
```

### 17. Conditional tasks

A task whose `when:` is already false when a host reaches it goes straight through to be skipped. It
takes no start slot, so a play full of conditional tasks only spaces the tasks that actually run.

```yaml
- name: Platform-specific setup
  hosts: all
  strategy: git_cgallagher.orchestration.time_offset
  vars:
    time_offset_seconds: 2
    time_offset_key: package-mirror
  tasks:
    - name: Install on Debian family
      ansible.builtin.apt:
        name: htop
      when: ansible_facts['os_family'] == 'Debian'

    - name: Install on Red Hat family
      ansible.builtin.dnf:
        name: htop
      when: ansible_facts['os_family'] == 'RedHat'
```

Only the hosts that run each install take a slot in the `package-mirror` queue.

### 18. A `when:` that reaches an expensive lookup

A `when:` that calls `lookup()` directly is handled automatically: the lookup runs once, on the worker. If
the lookup is reached *through a variable*, the plugin can't tell, and its up-front check runs the lookup
one extra time. Turn the check off where that matters:

```yaml
- name: Deploy only when the release gate is open
  hosts: app
  strategy: git_cgallagher.orchestration.time_offset
  vars:
    time_offset_seconds: 2
    release_gate: "{{ lookup('ansible.builtin.url', 'https://deploy.example.com/gate/' ~ inventory_hostname) }}"
  tasks:
    - name: Deploy
      ansible.builtin.copy:
        src: app.tar.gz
        dest: /opt/app/app.tar.gz
        mode: "0644"
      when: release_gate == 'open'
      vars:
        time_offset_check_when: false   # don't call the gate API twice
```

The trade-off: with the check off, a skipped task takes a start slot again.

### 19. Loops: spacing per item

A looped task is spaced per host: each host's whole loop starts in one slot. To space the items *within*
a host, use Ansible's own `loop_control.pause`:

```yaml
- name: Create several API tokens per host
  hosts: app
  strategy: git_cgallagher.orchestration.time_offset
  tasks:
    - name: Request tokens
      ansible.builtin.uri:
        url: https://auth.example.com/api/tokens
        method: POST
        body_format: json
        body:
          host: "{{ inventory_hostname }}"
          scope: "{{ item }}"
      loop: [read, write, admin]
      loop_control:
        pause: 1                       # 1 s between items on the same host
      delegate_to: localhost
      vars:
        time_offset_seconds: 2         # 2 s between hosts starting the loop
        time_offset_key: auth-api
```

---

## Real-world recipes

### 20. vSphere: clones that collide on guest customization

VMs cloned at the same instant can end up with duplicate UUIDs, and guest customization fails silently.
Space the clones, and cap how many run at once so vCenter isn't swamped.

```yaml
- name: Build Windows VMs
  hosts: new_vms
  gather_facts: false
  strategy: git_cgallagher.orchestration.time_offset
  tasks:
    - name: Clone, customize and power on
      community.vmware.vmware_guest:
        hostname: "{{ vcenter_hostname }}"
        name: "{{ inventory_hostname }}"
        template: win2022-template
        datacenter: DC1
        cluster: Cluster01
        folder: /DC1/vm/windows
        state: poweredon
        networks:
          - name: VM Network
            ip: "{{ ansible_host }}"
            netmask: 255.255.255.0
            gateway: 10.0.0.1
        customization:
          hostname: "{{ inventory_hostname_short }}"
          domain: example.com
        wait_for_customization: true
      delegate_to: localhost
      throttle: 5
      vars:
        time_offset_seconds: 10
        time_offset_jitter: 5
        time_offset_key: vcenter
```

### 21. Active Directory: domain lookups through one management server

See [example 9](#9-several-tasks-that-all-call-the-same-management-server). Because WinRM connection
setup varies by a second or more, keep the gap well above that.

### 22. Package mirrors and repositories

A fleet refreshing metadata at once can overload an internal mirror. Space the mirror-heavy tasks with a
shared key, and leave the rest of the play unspaced.

```yaml
- name: Monthly patching
  hosts: linux
  strategy: git_cgallagher.orchestration.time_offset
  tasks:
    - name: Mirror-heavy steps
      vars:
        time_offset_seconds: 4
        time_offset_jitter: 4
        time_offset_key: mirror
      block:
        - name: Refresh metadata and apply updates
          ansible.builtin.dnf:
            name: "*"
            state: latest
            update_cache: true

    - name: Report what changed (not offset)
      ansible.builtin.command: rpm -qa --last
      register: recent
      changed_when: false
```

### 23. Staggered reboots

Rebooting hosts a few seconds apart keeps shared storage, DHCP and the configuration management server
from seeing the whole fleet come back at once.

```yaml
- name: Staggered reboot
  hosts: hypervisors
  strategy: git_cgallagher.orchestration.time_offset
  tasks:
    - name: Reboot
      ansible.builtin.reboot:
        reboot_timeout: 1200
      throttle: 3                      # never more than 3 down at once
      vars:
        time_offset_seconds: 30
```

### 24. A rate-limited SaaS API

The provider allows about 2 requests per second for the whole account. One key covers every task that
calls it, whichever host runs them.

```yaml
- name: Sync hosts to the monitoring SaaS
  hosts: all
  gather_facts: false
  strategy: git_cgallagher.orchestration.time_offset
  vars:
    monitoring_api: https://api.monitoring.example.com/v2
  tasks:
    - name: Calls to the monitoring API
      delegate_to: localhost
      vars:
        time_offset_seconds: 0.5
        time_offset_key: monitoring-api
      block:
        - name: Look up the host
          ansible.builtin.uri:
            url: "{{ monitoring_api }}/hosts?name={{ inventory_hostname }}"
            headers:
              Authorization: "Bearer {{ monitoring_token }}"
          register: existing

        - name: Create it if missing
          ansible.builtin.uri:
            url: "{{ monitoring_api }}/hosts"
            method: POST
            headers:
              Authorization: "Bearer {{ monitoring_token }}"
            body_format: json
            body:
              name: "{{ inventory_hostname }}"
            status_code: [200, 201]
          when: existing.json.total == 0
```

`monitoring_token` comes from your vault or secrets store, not from this file.

### 25. Cloud provisioning API

Cloud APIs throttle bursts of create calls. Space them per region, so each region's API gets its own
queue.

```yaml
- name: Launch instances
  hosts: new_instances
  gather_facts: false
  strategy: git_cgallagher.orchestration.time_offset
  tasks:
    - name: Create the instance
      amazon.aws.ec2_instance:
        name: "{{ inventory_hostname }}"
        region: "{{ aws_region }}"
        instance_type: t3.medium
        image_id: "{{ ami_id }}"
        wait: true
      delegate_to: localhost
      vars:
        time_offset_seconds: 1
        time_offset_key: "ec2-{{ aws_region }}"
```

### 26. Git clones from one server

Many hosts cloning the same large repository at once can trip a Git server's connection limits.

```yaml
- name: Check out the application
  hosts: app
  strategy: git_cgallagher.orchestration.time_offset
  tasks:
    - name: Clone the repository
      ansible.builtin.git:
        repo: https://git.example.com/platform/app.git
        dest: /opt/app
        version: v4.2.0
      vars:
        time_offset_seconds: 2
        time_offset_key: git-server
```

### 27. Certificate requests to an internal CA

The CA processes requests one at a time, and a burst times some of them out.

```yaml
- name: Issue host certificates
  hosts: all
  strategy: git_cgallagher.orchestration.time_offset
  tasks:
    - name: Request a certificate
      ansible.builtin.uri:
        url: https://ca.example.com/api/v1/sign
        method: POST
        body_format: json
        body:
          common_name: "{{ inventory_hostname }}"
          csr: "{{ lookup('ansible.builtin.file', 'csr/' ~ inventory_hostname ~ '.csr') }}"
        return_content: true
      register: signed
      delegate_to: localhost
      vars:
        time_offset_seconds: 1.5
        time_offset_key: internal-ca
```

### 28. Database schema migration, one host first

Run the migration from one host, then roll the app out to the rest with spaced restarts. `run_once`
isn't supported under this strategy (as under `free`), so the migration gets its own play.

```yaml
- name: Migrate the schema from one host
  hosts: app[0]
  tasks:
    - name: Run migrations
      ansible.builtin.command: /opt/app/bin/migrate up
      register: migrate
      changed_when: "'No migrations' not in migrate.stdout"

- name: Restart the app everywhere, spaced
  hosts: app
  strategy: git_cgallagher.orchestration.time_offset
  vars:
    time_offset_seconds: 5
  tasks:
    - name: Restart
      ansible.builtin.service:
        name: app
        state: restarted
```

---

## When not to use it

- **You need to limit how many run at once, not when they start.** Use `throttle` on its own (or with an
  offset, as in [example 13](#13-throttle-space-the-starts-and-cap-how-many-run)).
- **You need batches.** Use `serial`, with or without an offset ([example 14](#14-serial-rolling-batches)).
- **Your play depends on hosts moving in lockstep.** This strategy is based on `free`: hosts move through
  tasks independently. A task that reads another host's facts or registered results through `hostvars`
  may run before that host has set them. `run_once`, `any_errors_fatal` and `max_fail_percentage` aren't
  supported, and neither are modules that bypass the host loop (such as `pause`). Keep such plays on
  `linear`, or split them as in [example 28](#28-database-schema-migration-one-host-first).
- **The collision happens at the target, after a slow connection.** The gap is measured when Ansible
  launches each task, before connecting. Over WinRM or slow SSH, size the gap well above the
  connection-time variation.

## Seeing what it's doing

Run with `-vv` to see every held task and every skipped one:

```text
<vm-07> time_offset: starting 'Clone and customize' in 4.98s
<web-03> time_offset: 'Install on Debian family' won't run (its 'when' is false), so it isn't held
```

The `TASK [...]` banner prints when a task is scheduled. A held task's result (`ok:`, `changed:`)
appears once it really runs.
