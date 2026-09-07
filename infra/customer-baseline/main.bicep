targetScope = 'resourceGroup'

@description('Azure region for operational resources.')
param location string = resourceGroup().location

@description('Short customer identifier used in resource names.')
@minLength(2)
@maxLength(12)
param customerCode string

@description('Log retention in days. Keep low in labs; set from contractual requirements in production.')
@minValue(30)
@maxValue(730)
param logRetentionDays int = 30

@description('Daily ingestion cap in GB. Use a deliberate production value after telemetry sizing.')
@minValue(1)
param dailyQuotaGb int = 1

@description('Email destination for operational alerts.')
param operationsEmail string

var normalizedCode = toLower(replace(customerCode, '-', ''))

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'law-${normalizedCode}-ops'
  location: location
  tags: {
    owner: operationsEmail
    environment: 'shared-operations'
    costCenter: 'managed-service'
  }
  properties: {
    retentionInDays: logRetentionDays
    workspaceCapping: {
      dailyQuotaGb: dailyQuotaGb
    }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-${normalizedCode}-operations'
  location: 'global'
  properties: {
    groupShortName: take(normalizedCode, 12)
    enabled: true
    emailReceivers: [
      {
        name: 'operations'
        emailAddress: operationsEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

output logAnalyticsWorkspaceId string = workspace.id
output actionGroupId string = actionGroup.id
output estimatedFixedMonthlyCostUsd string = 'No fixed estimate: Log Analytics is usage-based. Validate with the Azure pricing calculator and observed ingestion.'

