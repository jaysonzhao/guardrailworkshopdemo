# A dedicated OpenShift console ingress for Showroom

Use this procedure on OpenShift 4.x when participants need a separate console hostname inside the Showroom and the standard cluster console must keep its existing URL and framing policy. A platform administrator provisions a second IngressController and a small, workshop-managed instance of the release-matched OpenShift console frontend (`bridge`). Participants authenticate with the cluster's built-in OAuth server. Their existing OpenShift RBAC determines which projects and objects they can use.

| Browser destination | Where the request goes |
| --- | --- |
| Existing Showroom URL | Authenticated guide and Terminal |
| `console.workshops.cluster.example.com` | Workshop load balancer, workshop router, then the workshop console Service |
| Existing OAuth and identity-provider URLs | Cluster sign-in and consent, then back to the workshop console's own `/auth/callback` |
| Existing cluster console URL | Operator-managed console on its usual ingress |

Red Hat directs administrators to keep the platform administration Routes together. This procedure shards an application Route in an instructor-owned namespace. A second DNS name pointed at the existing console Service would still leave the frontend configured for its original OAuth callback. The workshop frontend is an independently maintained installation of the release’s console image. Use the core Kubernetes resource views with the participant identity. Dynamic plugins, operator-managed console customizations and monitoring integrations require their own configuration on this instance. [OpenShift ingress sharding](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html-single/ingress_and_load_balancing/ingress_and_load_balancing) · [Registering an OAuth client](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/authentication_and_authorization/configuring-oauth-clients) · [Upstream authenticated console example](https://github.com/openshift/console/tree/release-4.20/examples)

For a workshop that must show the exact existing console with all its plugins, an alternative is a dedicated HTTPS gateway for the **canonical** console hostname and a workshop-only DNS view for participant browsers. Terminate TLS with a valid certificate for that hostname, upstream to the existing router at a fixed origin that cannot resolve back to the gateway, preserve the console Host and TLS SNI, and validate upstream TLS. Scope the framing policy to the trusted Showroom origin at that gateway. Keep the OAuth hostname on normal DNS. This variant requires control of the browsers' DNS path. The procedure below gives participants a separate URL through native OpenShift ingress.

1. [Choose the hostname and certificate](#1-choose-the-workshop-hostname-and-certificate)
2. [Create the router and DNS record](#2-create-the-workshop-router-and-dns-record)
3. [Stage the Route](#3-stage-the-workshop-route)
4. [Register the OAuth client](#4-register-the-workshop-oauth-callback-and-trust-bundle)
5. [Start the frontend](#5-start-the-workshop-console-frontend)
6. [Connect the Showroom and verify SSO](#6-connect-the-showroom-and-verify-sso)

## 1. Choose the workshop hostname and certificate

Run the commands in one Bash session on an administrative workstation with `oc`, `jq` and `openssl`. This example uses a cloud platform that can provision a separate `LoadBalancerService`, such as AWS. Use `Internal` for browsers on the workshop network or VPN. Use `External` for a public roadshow where browsers must reach the load balancer over the internet. An internal load balancer must be reachable from the **participant's browser**. Framing permission controls which pages may embed it. Anyone able to connect directly still goes through cluster authentication and RBAC. On bare metal, provision a dedicated VIP with the site's supported load balancer or NodePort publishing strategy before continuing.

Use a unique router domain under your delegated cluster DNS zone. Obtain a browser-trusted wildcard certificate for that domain, for example `*.workshops.cluster.example.com`. A certificate for `*.apps.cluster.example.com` does not cover this hostname. The setup uses an instructor-owned namespace for the frontend and client Secret. Workshop users should have their normal project permissions, with write access to the frontend, its Secret and the trusted Showroom origin reserved for the appropriate operators.

~~~bash
set -euo pipefail
export LAB_NAMESPACE='your-participant-project'
export WORKSHOP_DOMAIN='workshops.cluster.example.com'
export LB_SCOPE='Internal'
export ROUTER_TLS_SECRET='showroom-console-router-tls'
export CONSOLE_HOST="console.$WORKSHOP_DOMAIN"
export WORKSHOP_CONSOLE_URL="https://$CONSOLE_HOST"

# For the standalone portal. For a managed portal, set the exact origin from its address bar.
showroom_host=$(oc -n "$LAB_NAMESPACE" get route grlab-showroom -o jsonpath='{.spec.host}')
test -n "$showroom_host"
export SHOWROOM_ORIGIN="https://$showroom_host"

oc whoami --show-server
oc get authentication.config.openshift.io cluster -o jsonpath='{.spec.type}{"\n"}'
oc get ingresscontroller -n openshift-ingress-operator \
  -o custom-columns='NAME:.metadata.name,DOMAIN:.status.domain'
~~~

The OAuthClient configuration below applies to `IntegratedOAuth`, including an external identity provider configured through OpenShift OAuth. The default type can be empty. A cluster using direct external `OIDC` authentication needs registration and configuration at that issuer. Verify the chosen domain and resource names are available or already belong to this workshop. Use an exact trusted `https://host[:port]` for `SHOWROOM_ORIGIN`, without a path or wildcard. For multiple portal origins, allow only the hostnames the platform owner trusts.

Create the namespace and certificate Secret once. An existing platform-managed certificate Secret in `openshift-ingress` can be referenced by setting `ROUTER_TLS_SECRET` to its name.

~~~bash
workshop_ns=$(oc get namespace showroom-console-system --ignore-not-found -o name)
if [[ -z "$workshop_ns" ]]; then
  oc create namespace showroom-console-system
fi
router_cert=$(oc -n openshift-ingress get secret "$ROUTER_TLS_SECRET" --ignore-not-found -o name)
if [[ -z "$router_cert" ]]; then
  oc -n openshift-ingress create secret tls "$ROUTER_TLS_SECRET" \
    --cert=workshop-fullchain.pem --key=workshop.key
fi
~~~

## 2. Create the workshop router and DNS record

This router matches the namespace's built-in name label and a label on the workshop Route. Its response-header actions apply only to Routes admitted by this isolated shard. In the tested Console response, the browser refused to render the page while `X-Frame-Options: DENY` was present, even with the Showroom origin in CSP. Delete X-Frame-Options on this dedicated router and set an exact-origin `frame-ancestors` policy. Do not apply this header policy to the shared default router. The `Set` action replaces the complete value of Content-Security-Policy; if the frontend needs other CSP directives, include them in this value and preserve the required policy. Inspect the final authenticated Console response in the target browser and release before publishing. [OpenShift HTTP header actions](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/ingress_and_load_balancing/routes) · [Browser framing rules](https://www.w3.org/TR/CSP/#frame-ancestors-and-frame-options)

~~~bash
cat <<EOF_ROUTER | oc apply --server-side --field-manager=showroom-console -f -
apiVersion: operator.openshift.io/v1
kind: IngressController
metadata:
  name: showroom-console
  namespace: openshift-ingress-operator
spec:
  domain: "$WORKSHOP_DOMAIN"
  replicas: 2
  namespaceSelector:
    matchLabels:
      kubernetes.io/metadata.name: showroom-console-system
  routeSelector:
    matchLabels:
      grlab-router: showroom-console
  defaultCertificate:
    name: "$ROUTER_TLS_SECRET"
  endpointPublishingStrategy:
    type: LoadBalancerService
    loadBalancer:
      scope: "$LB_SCOPE"
      dnsManagementPolicy: Unmanaged
  httpHeaders:
    actions:
      response:
        - name: X-Frame-Options
          action:
            type: Delete
        - name: Content-Security-Policy
          action:
            type: Set
            set:
              value: "frame-ancestors 'self' $SHOWROOM_ORIGIN"
EOF_ROUTER

oc -n openshift-ingress-operator wait ingresscontroller/showroom-console \
  --for=condition=Available --timeout=15m
oc -n openshift-ingress get service router-showroom-console \
  -o json | jq -r '.status.loadBalancer.ingress[]? | .hostname // .ip'
~~~

Point the **workshop console hostname** at the value printed above. Use a DNS CNAME or provider alias for a load balancer hostname, or an A/AAAA record for an address. This example deliberately manages DNS outside the Ingress Operator. Check the record and network reachability from a participant machine. Keep the ordinary cluster console and OAuth DNS records pointing at their existing destinations. [Ingress Operator settings](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/networking_operators/configuring-ingress)

### Make admission exclusive

The default router normally admits every application Route. A dedicated DNS record alone does not make a Route private. **Before publishing the workshop Route**, inspect the selectors on all existing controllers.

~~~bash
oc -n openshift-ingress-operator get ingresscontrollers -o json |
  jq '.items[] | {name: .metadata.name, namespaceSelector: .spec.namespaceSelector, routeSelector: .spec.routeSelector}'
~~~

For every other controller whose selectors would admit this Route, append the following expression to its existing `spec.namespaceSelector.matchExpressions` through the platform's normal configuration process. For example, use `oc -n openshift-ingress-operator edit ingresscontroller/default` if the default controller admits all namespaces. Preserve existing selectors and list entries. A controller that already excludes this namespace through its selectors needs no edit.

~~~yaml
# Append this entry under spec.namespaceSelector.matchExpressions
- key: kubernetes.io/metadata.name
  operator: NotIn
  values:
    - showroom-console-system
~~~

Wait for the affected router Deployments to finish rolling out. This change excludes only the workshop frontend's namespace. The platform console, OAuth and Showroom portal keep their existing ingress. The Route admission check below must show exactly one router before exposing the frontend. For a private workshop also confirm the dedicated hostname resolves to the internal load balancer from the workshop network.

## 3. Stage the workshop Route

Use a re-encrypt Route and a service-serving certificate. The stock OpenShift router can validate an OpenShift service-CA-signed backend using its default destination CA. Stage an empty Service first so the service CA can issue its certificate before the frontend starts. [OpenShift Route API](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/network_apis/route-route-openshift-io-v1)

~~~bash
cat <<EOF_ROUTE | oc -n showroom-console-system apply --server-side --field-manager=showroom-console -f -
apiVersion: v1
kind: Service
metadata:
  name: showroom-console
  annotations:
    service.beta.openshift.io/serving-cert-secret-name: showroom-console-serving
spec:
  selector:
    app: showroom-console
  ports:
    - name: https
      port: 443
      targetPort: https
---
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: showroom-console
  labels:
    grlab-router: showroom-console
spec:
  host: "$CONSOLE_HOST"
  to:
    kind: Service
    name: showroom-console
  port:
    targetPort: https
  tls:
    termination: reencrypt
    insecureEdgeTerminationPolicy: Redirect
EOF_ROUTE

# Route status is asynchronous. Run this check again after the frontend starts.
verify_showroom_admission() {
  local routers deadline
  deadline=$((SECONDS + 180))
  while :; do
    routers=$(oc -n showroom-console-system get route showroom-console -o json |
      jq -c '[.status.ingress[]? |
        select(any(.conditions[]?; .type == "Admitted" and .status == "True")) |
        .routerName] | unique') || return 1
    if [[ "$routers" == '["showroom-console"]' ]]; then
      printf 'Admitted routers: %s\n' "$routers"
      return 0
    fi
    if (( SECONDS >= deadline )); then
      printf 'Expected only showroom-console. Admitted routers: %s\n' "$routers" >&2
      return 1
    fi
    sleep 2
  done
}
verify_showroom_admission
~~~

If `default` or another router appears, correct its selector before creating the workload. If no router appears, inspect `oc -n showroom-console-system describe route showroom-console` and the new controller's conditions. HTTP 503 is expected while the Service has no ready pods.

## 4. Register the workshop OAuth callback and trust bundle

The cluster-scoped client has its own ID, secret and exact callback. The same secret is mounted only into the workshop frontend inside its protected namespace. The registration below is repeatable after an interruption and does not rotate an existing credential. Keep shell tracing off while running these commands.

~~~bash
set +x
export WORKSHOP_CALLBACK="$WORKSHOP_CONSOLE_URL/auth/callback"
existing=$(oc get oauthclient.oauth.openshift.io grlab-showroom-console --ignore-not-found -o name)
if [[ -z "$existing" ]]; then
  openssl rand -hex 32 |
    jq -Rs --arg callback "$WORKSHOP_CALLBACK" '{
      apiVersion: "oauth.openshift.io/v1", kind: "OAuthClient",
      metadata: {name: "grlab-showroom-console", labels: {"grlab-router": "showroom-console"}},
      grantMethod: "prompt", redirectURIs: [$callback], secret: rtrimstr("\n")
    }' | oc create -f -
fi

oc get oauthclient.oauth.openshift.io grlab-showroom-console -o json |
  jq -e --arg callback "$WORKSHOP_CALLBACK" '
    if .metadata.labels["grlab-router"] == "showroom-console" and .grantMethod == "prompt"
      and .redirectURIs == [$callback] and ((.secret // "") | length) > 0
    then {apiVersion: "v1", kind: "Secret", type: "Opaque",
          metadata: {name: "showroom-console-oauth"}, data: {"client-secret": (.secret | @base64)}}
    else error("OAuth client ownership, callback, grant or secret does not match the workshop")
    end' |
  oc -n showroom-console-system apply --server-side --field-manager=showroom-console -f -

oc -n openshift-config-managed get configmap oauth-serving-cert -o json |
  jq -e 'if ((.data["ca-bundle.crt"] // "") | contains("BEGIN CERTIFICATE"))
    then {apiVersion: "v1", kind: "ConfigMap", metadata: {name: "showroom-console-oauth-ca"},
          data: {"ca-bundle.crt": .data["ca-bundle.crt"]}}
    else error("Obtain the trusted CA bundle for the cluster OAuth endpoint")
    end' |
  oc -n showroom-console-system apply --server-side --field-manager=showroom-console -f -
~~~

The OAuth CA bundle comes from the Cluster Authentication Operator. If your issuer uses site-managed trust and the published bundle is unavailable, have the platform owner populate `showroom-console-oauth-ca` in `showroom-console-system` with the verified PEM CA chain under `ca-bundle.crt`. Refresh the workshop copy and restart the workshop frontend when that bundle changes. The console's OpenShift login requests the participant's own `user:full` scope. Their user permissions still govern requests. Use a dedicated participant browser profile. [OpenShift authentication and authorization](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html-single/authentication_and_authorization/index) · [Console authentication configuration](https://github.com/openshift/console/blob/release-4.20/cmd/bridge/config/auth/authoptions.go)

## 5. Start the workshop console frontend

Take the image from the cluster's running release. A dedicated ServiceAccount supplies the in-cluster API connection. Browser requests to Kubernetes use the signed-in participant token. The deployment uses one frontend replica because the 4.20 OpenShift authentication mode generates session keys per process. Routers can still have two replicas. Restarting the frontend can require a fresh console sign-in.

~~~bash
export CONSOLE_IMAGE
CONSOLE_IMAGE=$(oc -n openshift-console get deployment console \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="console")].image}')
test -n "$CONSOLE_IMAGE"

cat <<EOF_FRONTEND | oc -n showroom-console-system apply --server-side --field-manager=showroom-console -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: showroom-console
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: showroom-console-service-ca
  annotations:
    service.beta.openshift.io/inject-cabundle: "true"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: showroom-console
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: showroom-console
  template:
    metadata:
      labels:
        app: showroom-console
    spec:
      serviceAccountName: showroom-console
      securityContext:
        runAsNonRoot: true
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: console
          image: "$CONSOLE_IMAGE"
          command: ["/opt/bridge/bin/bridge"]
          args:
            - --public-dir=/opt/bridge/static
            - --listen=https://0.0.0.0:8443
            - --tls-cert-file=/var/serving/tls.crt
            - --tls-key-file=/var/serving/tls.key
            - --service-ca-file=/var/service-ca/service-ca.crt
            - --k8s-mode=in-cluster
            - --user-auth=openshift
            - --user-auth-oidc-client-id=grlab-showroom-console
            - --user-auth-oidc-client-secret-file=/var/client/client-secret
            - --user-auth-oidc-ca-file=/var/oauth-ca/ca-bundle.crt
            - --base-address=$WORKSHOP_CONSOLE_URL
            - --branding=ocp
            - --user-settings-location=localstorage
          ports:
            - {name: https, containerPort: 8443}
          readinessProbe:
            httpGet: {path: /health, port: https, scheme: HTTPS}
            periodSeconds: 5
          resources:
            requests: {cpu: 50m, memory: 128Mi}
          securityContext:
            allowPrivilegeEscalation: false
            capabilities: {drop: [ALL]}
          volumeMounts:
            - {name: serving, mountPath: /var/serving, readOnly: true}
            - {name: client, mountPath: /var/client, readOnly: true}
            - {name: oauth-ca, mountPath: /var/oauth-ca, readOnly: true}
            - {name: service-ca, mountPath: /var/service-ca, readOnly: true}
      volumes:
        - name: serving
          secret: {secretName: showroom-console-serving}
        - name: client
          secret: {secretName: showroom-console-oauth}
        - name: oauth-ca
          configMap: {name: showroom-console-oauth-ca}
        - name: service-ca
          configMap:
            name: showroom-console-service-ca
            items:
              - {key: service-ca.crt, path: service-ca.crt}
EOF_FRONTEND

oc -n showroom-console-system rollout status deployment/showroom-console --timeout=5m
verify_showroom_admission
oc get clusteroperators ingress console authentication
~~~

This lab uses the upstream browser-local preferences option to avoid giving this ServiceAccount permission to manage the platform console's user-settings namespace. The **Terminal** tab continues to use the authenticated Showroom terminal. Refresh the release image and recheck the frontend, authentication and headers after an OpenShift upgrade.

## 6. Connect the Showroom and verify SSO

First navigate to `$WORKSHOP_CONSOLE_URL` in the participant's browser. Authenticate as the assigned user, approve the workshop client if prompted, and confirm that the resulting address uses `$CONSOLE_HOST`. Open the participant project's **Pods** page. A return to the ordinary console host signals a mismatch between the frontend base address, callback and published URL.

Set the Showroom embed value explicitly. `oc whoami --show-console` continues to report the standard console.

~~~bash
export SHOWROOM_CONSOLE_EMBED_URL="$WORKSHOP_CONSOLE_URL/k8s/ns/$LAB_NAMESPACE/pods"
curl -fsS -D - -o /dev/null "$WORKSHOP_CONSOLE_URL/" |
  awk 'tolower($0) ~ /^http\/|^content-security-policy:|^x-frame-options:/'
~~~

For a fresh standalone deployment, run `./grlab showroom` with this variable exported. For a running standalone portal, follow **Set the standalone portal to iframe mode** in the deployment skill to patch `grlab-showroom-settings`, back up and restore appendix selections, and restart only `deployment/grlab-showroom`. Use the dedicated URL in place of console discovery. In a managed RHDP Showroom, set the **OCP Console** tab's `url` to the dedicated HTTPS address in `ui-config.yml` and redeploy through its provisioner. This dedicated path needs no cluster-wide RHDP console-embed role.

Return to the guide in the same browser tab and click **OCP Console**. Check that the guide remains visible, the browser tab count stays the same, and the embedded console lists pods in the assigned project. Compare its user menu to `oc whoami` in **Terminal**. If a login screen refuses framing on first access or after expiry, visit the **workshop** console at the top level in this same tab, sign in, then return to the Showroom. In DevTools, inspect the final authenticated console document for the exact `frame-ancestors` origin. A health check or anonymous redirect alone does not verify user identity, admission exclusivity or iframe rendering.

## Retire the dedicated ingress

First repoint the Showroom tab and save active terminal work if this requires a portal restart. Remove the workshop DNS record and workshop Route. Retire the workshop-specific OAuth client, frontend, Service and dedicated router through their owning configuration. Deleting the dedicated IngressController retires its load balancer according to the platform's policy. After the workshop Route is gone, remove only the namespace exclusion added for it. Preserve resources shared with other workshops, including shared certificates. The lab's NeMo cleanup command controls the banking application and has a separate scope.
