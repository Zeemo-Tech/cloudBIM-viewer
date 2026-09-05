import unittest

from analysis_mesh.contracts import AlgorithmDescriptor, AlgorithmRegistry, ContractError


class Builder:
    descriptor = AlgorithmDescriptor("x", "X", "1", "1", (), {"amount": {"type": "number", "minimum": 1}}, {"amount": 2})
    def build(self, stream, effectiveParameters, workspace): return stream


class ContractsTest(unittest.TestCase):
    def test_registry_duplicate_and_unknown_parameters_rejected(self):
        registry = AlgorithmRegistry(); registry.register(Builder())
        with self.assertRaises(ContractError): registry.register(Builder())
        with self.assertRaises(ContractError): Builder.descriptor.effective_parameters({"typo": 1})
        self.assertEqual(Builder.descriptor.effective_parameters({})["amount"], 2)
