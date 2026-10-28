"""Offline manifest tests: only helm template is invoked, never a cluster."""
import copy
import pathlib
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
