## kool-dev/charts

Some cool charts around here!!

### Service availability (generic 1.10.0+)

The generic chart accepts opt-in Deployment spreading and disruption budgets:

```yaml
replicaCount: 3
nodeSelector:
  workload-pool: apps
spread:
  mode: required
  topologyKey: kubernetes.io/hostname
  maxSkew: 1
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxUnavailable: 0
    maxSurge: 1
disruptionBudget:
  minAvailable: 1
```

`spread.mode` is `"off"`, `preferred` (`ScheduleAnyway`), or `required`
(`DoNotSchedule`). When enabled, topology defaults to hostname and maxSkew to 1.
Omission leaves scheduling unchanged. Spreading and the `policy/v1` PDB use the
existing Deployment selector labels, including the chart alias and release, so
other services, hook Jobs, and CronJobs do not contribute to the counts.

`disruptionBudget` requires exactly one of `minAvailable` or `maxUnavailable`:
a non-negative integer or a quoted percentage from `"0%"` to `"100%"`. Omit it to
create no budget. Zero is preserved. Budgets can block drain, do not control
Deployment rollouts, and do not protect against host failure or direct deletion.

Spreading only considers eligible nodes and does not rebalance existing pods.
No `minDomains` is set: required spreading does not guarantee two available
topology domains and can either permit concentration or leave replacements
Pending as node eligibility changes. Ensure node labels, capacity, readiness,
storage, and control-plane availability separately. Avoid combining cross-node
spreading with required same-node affinity or a shared ReadWriteOnce volume.

Offline manifest tests (Helm and Python with PyYAML required):

```sh
python -m unittest discover -s tests -v
```
