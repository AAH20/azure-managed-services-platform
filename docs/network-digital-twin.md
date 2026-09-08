# Azure hybrid and multi-cloud network digital twin

This module compares current and proposed network behavior against business connectivity intents.
It complements Azure Network Watcher, Azure Virtual Network Manager Network Verifier, Batfish and
network sources of truth; it does not replace their discovery or forwarding models.

## Assurance loop

1. Define required and prohibited flows with protocol, port, DNS, transit and latency requirements.
2. Import current and proposed path evidence from Azure, cloud and network-analysis adapters.
3. Test forward/return reachability, DNS scope and required inspection transit.
4. Identify behavioral regressions rather than treating a valid configuration as a safe change.
5. Calculate contribution margin dependent on regressed application paths.
6. Block the change or send it to Change Assurance for active canary validation.

The synthetic Terraform scenario introduces a wrong next hop, public DNS resolution for a private
endpoint and a missing DR return prefix. The engine blocks the change and executes no mutation.

## Production adapters

- Azure Resource Graph, Network Watcher and Virtual Network Manager Network Verifier
- Azure Firewall, NSGs, routes, Private DNS, Private Link and Virtual WAN
- ExpressRoute, VPN, SD-WAN and Batfish current/proposed snapshots
- Cisco, Fortinet, Palo Alto, Juniper, NetBox and Nautobot
- Terraform/OpenTofu plans, Ansible diffs and Kubernetes network policies
- OpenTelemetry transactions and the platform's incident, migration and recovery modules

## KPI and commercial boundary

Track intent coverage, unknown paths, flow conformance, DNS validation, asymmetric paths,
pre-change defects, network MTTR, rollback rate and contribution margin at risk. Indicative ranges
are USD 7,500–25,000 for discovery, USD 15,000–50,000 for intent implementation, USD
30,000–150,000 for a hybrid/multi-cloud twin and USD 5,000–25,000 monthly for managed network
reliability. These are positioning ranges, not quotes or guaranteed outcomes.
