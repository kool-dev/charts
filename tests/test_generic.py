"""Offline manifest tests: only helm template is invoked, never a cluster."""
import copy
import pathlib
import shutil
import subprocess
import tempfile
import unittest

import yaml


CHART = pathlib.Path(__file__).resolve().parents[1] / "charts" / "generic"
CONTAINER = {
    "image": {"repository": "nginx", "tag": "latest"},
    "ports": [{"containerPort": 80}],
    "livenessProbe": {"tcpSocket": {"port": 80}},
}


def render(values):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml") as stream:
        yaml.safe_dump(values, stream)
        stream.flush()
        output = subprocess.check_output(
            ["helm", "template", "test", str(CHART), "-f", stream.name], text=True
        )
    return [doc for doc in yaml.safe_load_all(output) if doc]


class GenericAPIVersionsTest(unittest.TestCase):
    def test_ingress_uses_stable_api_and_backend_shape(self):
        for path in ({"serviceName": "web", "servicePort": 8080},
                     {"fullServiceName": "external-web", "servicePort": 80,
                      "path": "/health", "pathType": "Exact"}):
            with self.subTest(path=path):
                documents = render({
                    "containers": {"default": CONTAINER},
                    "ingresses": [{
                        "name": "web", "ingressClassName": "nginx",
                        "hosts": [{"host": "example.test", "paths": [path]}],
                        "tls": [{"hosts": ["example.test"], "secretName": "web-tls"}],
                    }],
                })
                ingress = next(d for d in documents if d["kind"] == "Ingress")
                self.assertEqual(ingress["apiVersion"], "networking.k8s.io/v1")
                spec = ingress["spec"]
                self.assertEqual(spec["ingressClassName"], "nginx")
                self.assertEqual(spec["tls"], [{"hosts": ["example.test"], "secretName": "web-tls"}])
                self.assertEqual(spec["rules"][0]["host"], "example.test")
                rendered_path = spec["rules"][0]["http"]["paths"][0]
                self.assertEqual(rendered_path["path"], path.get("path", "/"))
                self.assertEqual(rendered_path["pathType"], path.get("pathType", "Prefix"))
                self.assertEqual(rendered_path["backend"], {"service": {
                    "name": path.get("fullServiceName", "test-generic-web"),
                    "port": {"number": path["servicePort"]},
                }})

    def test_rbac_uses_stable_api_and_matching_service_account(self):
        rules = [{"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list"]}]
        for name in ("", "custom-account"):
            with self.subTest(name=name):
                documents = render({
                    "containers": {"default": CONTAINER},
                    "serviceAccount": {"create": True, "name": name,
                                       "role": {"create": True, "rules": rules}},
                })
                account = next(d for d in documents if d["kind"] == "ServiceAccount")
                role = next(d for d in documents if d["kind"] == "Role")
                binding = next(d for d in documents if d["kind"] == "RoleBinding")
                expected_name = name or "test-generic"
                self.assertEqual(account["metadata"]["name"], expected_name)
                for resource in (role, binding):
                    self.assertEqual(resource["apiVersion"], "rbac.authorization.k8s.io/v1")
                    self.assertEqual(resource["metadata"]["name"], expected_name)
                self.assertEqual(role["rules"], rules)
                self.assertEqual(binding["roleRef"], {
                    "kind": "Role", "name": expected_name,
                    "apiGroup": "rbac.authorization.k8s.io",
                })
                self.assertEqual(binding["subjects"], [{
                    "kind": "ServiceAccount", "name": expected_name, "namespace": "default",
                }])


class GenericServiceTest(unittest.TestCase):
    def test_publish_not_ready_addresses(self):
        for headless in (True, False):
            for value in (True, False, None):
                with self.subTest(headless=headless, value=value):
                    service = {"type": "ClusterIP", "ports": [{"port": 8107}]}
                    if headless:
                        service["clusterIP"] = "None"
                    if value is not None:
                        service["publishNotReadyAddresses"] = value
                    readiness = {"httpGet": {"path": "/health", "port": 8108}}
                    container = dict(copy.deepcopy(CONTAINER), readinessProbe=readiness)
                    documents = render({"services": {"peers": service},
                                        "containers": {"default": container}})
                    spec = next(doc["spec"] for doc in documents if doc["kind"] == "Service")
                    if value is None:
                        self.assertNotIn("publishNotReadyAddresses", spec)
                    else:
                        self.assertIs(spec["publishNotReadyAddresses"], value)
                    self.assertEqual(spec.get("clusterIP"), "None" if headless else None)
                    deployment = next(doc for doc in documents if doc["kind"] == "Deployment")
                    self.assertEqual(deployment["spec"]["template"]["spec"]["containers"][0]
                                     ["readinessProbe"], readiness)


class GenericSchedulingTest(unittest.TestCase):
    def test_defaults_and_off(self):
        for spread in (None, {"mode": "off"}):
            values = {"containers": {"default": CONTAINER}}
            if spread:
                values["spread"] = spread
            documents = render(values)
            deployment = next(d for d in documents if d["kind"] == "Deployment")
            self.assertNotIn("topologySpreadConstraints", deployment["spec"]["template"]["spec"])
            self.assertFalse(any(d["kind"] == "PodDisruptionBudget" for d in documents))

    def test_spread_and_budget_select_only_the_deployment(self):
        for mode, policy in (("required", "DoNotSchedule"), ("preferred", "ScheduleAnyway")):
            for budget in ({"minAvailable": 0}, {"minAvailable": "50%"}, {"maxUnavailable": 1}):
                with self.subTest(mode=mode, budget=budget):
                    documents = render({"replicaCount": 3, "spread": {"mode": mode},
                                        "disruptionBudget": budget, "containers": {"default": CONTAINER}})
                    deployment = next(d for d in documents if d["kind"] == "Deployment")
                    selector = deployment["spec"]["selector"]["matchLabels"]
                    self.assertEqual(selector, deployment["spec"]["template"]["metadata"]["labels"])
                    spread = deployment["spec"]["template"]["spec"]["topologySpreadConstraints"][0]
                    self.assertEqual(spread, {"maxSkew": 1, "topologyKey": "kubernetes.io/hostname",
                                             "whenUnsatisfiable": policy, "labelSelector": {"matchLabels": selector}})
                    pdb = next(d for d in documents if d["kind"] == "PodDisruptionBudget")
                    self.assertEqual(pdb["apiVersion"], "policy/v1")
                    self.assertEqual(pdb["metadata"]["name"], deployment["metadata"]["name"])
                    self.assertEqual(pdb["spec"], dict(budget, selector={"matchLabels": selector}))

    def test_aliases_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = pathlib.Path(directory)
            dependencies = []
            for alias in ("web", "horizon"):
                shutil.copytree(CHART, parent / "charts" / alias)
                dependencies.append({"name": "generic", "version": "1.10.0", "alias": alias})
            (parent / "Chart.yaml").write_text(yaml.safe_dump(
                {"apiVersion": "v2", "name": "deploy", "version": "1.0.0", "dependencies": dependencies}))
            (parent / "values.yaml").write_text(yaml.safe_dump({
                alias: {"spread": {"mode": "required", "maxSkew": 2, "topologyKey": "topology.kubernetes.io/zone"},
                        "disruptionBudget": {"minAvailable": 1}, "containers": {"default": CONTAINER},
                        "jobs": {"hook": dict(CONTAINER, hook="post-install", restartPolicy="Never")},
                        "cronjobs": {"cron": dict(CONTAINER, schedule="* * * * *", restartPolicy="Never")}}
                for alias in ("web", "horizon")}))
            documents = list(yaml.safe_load_all(subprocess.check_output(
                ["helm", "template", "test", str(parent)], text=True)))
        deployments = [d for d in documents if d and d["kind"] == "Deployment"]
        self.assertEqual(len(deployments), 2)
        selectors = [d["spec"]["selector"]["matchLabels"] for d in deployments]
        self.assertNotEqual(*selectors)
        budgets = [d for d in documents if d and d["kind"] == "PodDisruptionBudget"]
        self.assertEqual(len(budgets), 2)
        for deployment in deployments:
            budget = next(d for d in budgets if d["metadata"]["name"] == deployment["metadata"]["name"])
            self.assertEqual(budget["spec"]["selector"], deployment["spec"]["selector"])
            spread = deployment["spec"]["template"]["spec"]["topologySpreadConstraints"][0]
            self.assertEqual(spread["labelSelector"]["matchLabels"], deployment["spec"]["selector"]["matchLabels"])
            self.assertEqual(spread["maxSkew"], 2)
            self.assertEqual(spread["topologyKey"], "topology.kubernetes.io/zone")
        for document in documents:
            if not document or document["kind"] not in ("Job", "CronJob"):
                continue
            template = (document["spec"]["template"] if document["kind"] == "Job"
                        else document["spec"]["jobTemplate"]["spec"]["template"])
            self.assertNotIn("topologySpreadConstraints", template["spec"])
            for selector in selectors:
                self.assertFalse(all(template["metadata"]["labels"].get(k) == v for k, v in selector.items()))

    def test_invalid_settings_fail(self):
        for options in ({"spread": {"mode": "typo"}}, {"spread": {"mode": "required", "maxSkew": 0}},
                        {"spread": {"mode": "required", "maxSkew": "1"}},
                        {"spread": {"mode": "required", "unknown": True}},
                        {"spread": {"mode": "required", "topologyKey": "bad key"}},
                        {"spread": {}}, {"disruptionBudget": {}},
                        {"disruptionBudget": {"minAvailable": 1, "maxUnavailable": 1}},
                        {"disruptionBudget": {"minAvailable": True}},
                        {"disruptionBudget": {"minAvailable": "101%"}}):
            with self.subTest(options=options), self.assertRaises(subprocess.CalledProcessError):
                render(dict(options, containers={"default": CONTAINER}))


class GenericLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.values = {"containers": {"default": copy.deepcopy(CONTAINER)}}

    def deployment(self):
        return next(doc for doc in render(self.values) if doc["kind"] == "Deployment")

    def test_omitted_settings_keep_defaults_and_names(self):
        deployment = self.deployment()
        self.assertEqual(deployment["metadata"]["name"], "test-generic")
        self.assertEqual(deployment["spec"]["replicas"], 1)
        self.assertNotIn("strategy", deployment["spec"])
        self.assertNotIn("minReadySeconds", deployment["spec"])
        pod = deployment["spec"]["template"]["spec"]
        self.assertNotIn("terminationGracePeriodSeconds", pod)
        container = pod["containers"][0]
        self.assertNotIn("lifecycle", container)
        self.assertNotIn("startupProbe", container)
        self.assertEqual(container["readinessProbe"], container["livenessProbe"])

    def test_deployment_and_pod_settings(self):
        self.values.update(terminationGracePeriodSeconds=90, minReadySeconds=5)
        deployment = self.deployment()
        self.assertEqual(deployment["spec"]["minReadySeconds"], 5)
        self.assertEqual(deployment["spec"]["template"]["spec"]["terminationGracePeriodSeconds"], 90)
        self.assertNotIn("terminationGracePeriodSeconds", deployment["spec"])

    def test_explicit_zero_values(self):
        self.values.update(terminationGracePeriodSeconds=0, minReadySeconds=0, replicaCount=0)
        deployment = self.deployment()
        self.assertEqual(deployment["spec"]["minReadySeconds"], 0)
        self.assertEqual(deployment["spec"]["replicas"], 0)
        self.assertEqual(deployment["spec"]["template"]["spec"]["terminationGracePeriodSeconds"], 0)

    def test_rolling_update_preserves_zero_and_percentages(self):
        for rolling in ({"maxUnavailable": 0, "maxSurge": "25%"},
                        {"maxUnavailable": 1, "maxSurge": 0}):
            with self.subTest(rolling=rolling):
                self.values["strategy"] = {"type": "RollingUpdate", "rollingUpdate": rolling}
                self.assertEqual(self.deployment()["spec"]["strategy"], self.values["strategy"])

    def test_recreate_strategy(self):
        self.values["strategy"] = {"type": "Recreate"}
        self.assertEqual(self.deployment()["spec"]["strategy"], {"type": "Recreate"})

    def test_container_settings_do_not_leak(self):
        lifecycle = {"preStop": {"exec": {"command": ["sh", "-c", "sleep 10"]}}}
        startup = {"httpGet": {"path": "/up", "port": 80}, "initialDelaySeconds": 0,
                   "periodSeconds": 2, "failureThreshold": 30}
        readiness = {"httpGet": {"path": "/up", "port": 80}}
        self.values["containers"]["default"].update(
            lifecycle=lifecycle, startupProbe=startup, readinessProbe=readiness
        )
        self.values["containers"]["worker"] = copy.deepcopy(CONTAINER)
        # Even if supplied here, app-only fields are not rendered for these workloads.
        self.values["initContainers"] = {"init": self.values["containers"]["default"]}
        self.values["jobs"] = {"hook": dict(self.values["containers"]["default"],
                                          hook="post-install", restartPolicy="Never")}
        self.values["cronjobs"] = {"cron": dict(self.values["containers"]["default"],
                                              schedule="* * * * *", restartPolicy="Never")}
        documents = render(self.values)
        deployment = next(doc for doc in documents if doc["kind"] == "Deployment")
        pod = deployment["spec"]["template"]["spec"]
        app = next(c for c in pod["containers"] if c["name"] == "default")
        self.assertEqual(app["lifecycle"], lifecycle)
        self.assertEqual(app["startupProbe"], startup)
        self.assertEqual(app["readinessProbe"], readiness)
        others = pod["initContainers"] + [c for c in pod["containers"] if c["name"] != "default"]
        for doc in documents:
            if doc["kind"] == "Job":
                others += doc["spec"]["template"]["spec"]["containers"]
            if doc["kind"] == "CronJob":
                others += doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
        self.assertEqual(len(others), 4)
        for container in others:
            self.assertNotIn("lifecycle", container)
            self.assertNotIn("startupProbe", container)


if __name__ == "__main__":
    unittest.main()
