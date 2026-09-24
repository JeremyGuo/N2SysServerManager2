// Labels include PCI to disambiguate same-named interfaces. Never use substring matching.
export function interfaceLabel(item) {
  return `${item.interface}${item.pci_address ? ` (PCI: ${item.pci_address})` : ''}`
}
export function resolveInterface(interfaces, label, selectedId = null) {
  const matches = interfaces.filter(item => interfaceLabel(item) === label && (selectedId == null || item.id === selectedId))
  return matches.length === 1 ? matches[0] : null
}
