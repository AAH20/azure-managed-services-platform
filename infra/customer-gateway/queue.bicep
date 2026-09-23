targetScope = 'resourceGroup'

@description('Globally unique Service Bus namespace name for one customer environment.')
param serviceBusNamespaceName string

@description('Queue name for authenticated alert intake after the Azure transport adapter is added.')
param serviceBusQueueName string = 'a2z-operations-alerts'

@description('Location for the Service Bus namespace.')
param location string = resourceGroup().location

resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2024-01-01' = {
  name: serviceBusNamespaceName
  location: location
  sku: {
    name: 'Standard'
  }

  resource queue 'queues' = {
    name: serviceBusQueueName
    properties: {
      requiresDuplicateDetection: true
      duplicateDetectionHistoryTimeWindow: 'P1D'
      maxDeliveryCount: 5
      lockDuration: 'PT1M'
      deadLetteringOnMessageExpiration: true
      defaultMessageTimeToLive: 'P7D'
    }
  }
}

output queueResourceId string = serviceBusNamespace::queue.id
output namespaceName string = serviceBusNamespace.name
