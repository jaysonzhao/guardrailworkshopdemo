# Sources and adaptations

The LangGraph banking agent, its playground, synthetic account tool, banking actions, Colang flows, classifier prompts and GPU workload derive from the Apache-2.0 project credited below. This workshop uses the private fork at revision 3b01ec29a7b3a037dd0d3640df8e14c3e6da0ae9, branch codex/guardrailagentdeploy, under agents/langgraph/examples/guardrailed_agent. The starter kit and logo resource originate in the Red Hat Data Services project. Red Hat trademarks belong to their owners.

- [Banking agent fork and source revision](https://github.com/jaysonzhao/guardrailagentdeploy/tree/3b01ec29a7b3a037dd0d3640df8e14c3e6da0ae9/agents/langgraph/examples/guardrailed_agent)
- [Public starter kit](https://github.com/red-hat-data-services/agentic-starter-kits)
- [Showroom layout and navigation reference](https://github.com/rhpds/ocp-dev-days-rdshw-showroom/tree/6c5bbb86ea6e7e62fec013da090f8f8b72a3e4e9)
- [Showroom UI theme and corresponding sources](https://github.com/rhpds/rhdp_showroom_theme). The unmodified UI bundle has SHA-256 73e3a16a4ebdf6139e64ef8cda3cde8e4b20374bc9be19faf929945d2153502b. Its Mozilla Public License 2.0 is in showroom/LICENSE-theme. The guide pages and workshop scripts were written for this lab.
- [NVIDIA model card and model terms](https://huggingface.co/nvidia/Llama-3.1-Nemotron-Safety-Guard-8B-v3/tree/8fdc246ba3d56db9c469d534233b9f582d3afafa). The optional model is fetched separately.

Workshop adaptations include direct and proxied readiness, synthetic tool authorization, a module-specific scenario picker, portable image builds, separately activated rail layers, named Colang refusal messages, strict Nemotron JSON verdicts, and a portable GPU resource request. The fork's CPU topic classifier and MLflow environment are outside the workshop sequence.

The standalone tab layout uses a local portal and ttyd. The terminal build verifies the upstream binary against the release's published SHA-256 for its architecture. OpenShift supplies its own release-matched OAuth proxy and CLI images. The project-scoped OAuth client and the console framing behavior follow these sources.

- [ttyd 1.7.7 release, checksums and license](https://github.com/tsl0922/ttyd/releases/tag/1.7.7)
- [OpenShift OAuth proxy](https://github.com/openshift/oauth-proxy/tree/release-4.20)
- [OpenShift service accounts as OAuth clients and role scopes](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/observability/authentication_and_authorization/configuring-identity-providers#using-service-accounts-as-oauth-client)
- [RHDP Showroom tab configuration](https://rhpds.github.io/showroom_template_nookbag/modules/ui-config.html)
- [RHDP OpenShift console embedding](https://rhpds.github.io/showroom_template_nookbag/modules/ocp-integration.html)
