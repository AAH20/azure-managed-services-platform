# Initial control catalog

| ID | Control | Evidence | Default response |
|---|---|---|---|
| AZ-MSP-001 | Ownership metadata | Resource tags | Review-gated IaC tag assignment |
| AZ-MSP-002 | VM monitoring coverage | Inventory assertion from approved adapter | Monitoring onboarding proposal |
| AZ-MSP-003 | VM backup coverage | Backup protection state | RPO selection and onboarding proposal |
| AZ-MSP-004 | Patch assessment freshness | Assessment age | Assessment and maintenance-window proposal |
| AZ-MSP-005 | Storage network exposure | `publicNetworkAccess` | Dependency review before private-access change |

The fixture assertions are synthetic. A production adapter must preserve the Azure resource ID,
API version, query, collection timestamp and raw response hash used to derive each assertion.

