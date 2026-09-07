targetScope = 'subscription'

@description('Microsoft Entra tenant ID of the managing service provider.')
param managedByTenantId string

@description('Display name visible to the customer.')
param registrationName string = 'A2Z SOC Azure Managed Services'

@description('Permanent GUID used by the registration definition.')
param registrationDefinitionGuid string

@description('Permanent GUID used by the registration assignment.')
param registrationAssignmentGuid string

@description('Principal object ID in the managing tenant.')
param principalId string

@description('Least-privilege Azure built-in role definition GUID. Reader by default.')
param roleDefinitionGuid string = 'acdd72a7-3385-48ef-bd42-f606fba81ae7'

resource registrationDefinition 'Microsoft.ManagedServices/registrationDefinitions@2022-10-01' = {
  name: registrationDefinitionGuid
  properties: {
    registrationDefinitionName: registrationName
    description: 'Customer-revocable delegated access for managed Azure operations.'
    managedByTenantId: managedByTenantId
    authorizations: [
      {
        principalId: principalId
        principalIdDisplayName: 'A2Z SOC Azure Operations'
        roleDefinitionId: roleDefinitionGuid
      }
    ]
  }
}

resource registrationAssignment 'Microsoft.ManagedServices/registrationAssignments@2022-10-01' = {
  name: registrationAssignmentGuid
  properties: {
    registrationDefinitionId: registrationDefinition.id
  }
}

output registrationDefinitionId string = registrationDefinition.id
output delegatedRoleDefinitionGuid string = roleDefinitionGuid

