## generic

Kool generic chart, you can deploy pretty much anything using this little guy.


### Upgrading to 1.4

Service signature has changed in order to support multiple ports in the same service:

```diff
-services:
-  default:
-    ...
-    port: 80
-    # targetPort: 80
+services:
+  default:
+    ...
+    ports:
+      - port: 80
+        # targetPort: 80
```
