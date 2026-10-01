import unittest
from helpers import create, fixture, spec_of


def azure_files(raw: dict, region: str | None = None) -> dict:
    s = spec_of(raw, 'azure', region)
    assert not s.errors, s.errors
    return create.AzureProject(s).files()


class AzureMappingTest(unittest.TestCase):
    """Each component becomes the resources of the lab's validated scratch/azure-full/main.tf (cited per test), with
    the azurerm 5 forms (research §1: rbac_authorization_enabled, public_network_access, storage_account_id, …)."""

    @classmethod
    def setUpClass(cls):
        cls.cp = azure_files(fixture('customer-portal.json'))                 # francecentral via the region pairs
        cls.tools = azure_files(fixture('internal-tools.json'))               # westus: no availability zones
        cls.mixed = azure_files(fixture('azure-mixed-apps.json'))
        cls.archive = azure_files(fixture('storage-only.json'))
        cls.shop = azure_files(fixture('all-options.json'), 'eastus')

    def test_versions_and_provider(self):   # azurerm 5: no auto-registration; storage_use_azuread for keyless storage
        v = self.cp['versions.tf']
        self.assertIn('version = "~> 5.0"', v)
        self.assertIn('    random = {\n      source  = "hashicorp/random"\n      version = "~> 3.7"', v)
        p = self.cp['providers.tf']
        self.assertIn('resource_providers_to_register = ["Microsoft.App", "Microsoft.DBforPostgreSQL", "Microsoft.KeyVault", '
                      '"Microsoft.ManagedIdentity", "Microsoft.Network", "Microsoft.OperationalInsights", "Microsoft.Storage"]', p)
        self.assertIn('storage_use_azuread            = true\n  features {}', p)
        self.assertIn('["Microsoft.KeyVault", "Microsoft.Network", "Microsoft.Storage"]', self.archive['providers.tf'])

    def test_network_only_the_subnets_needed(self):
        n = self.cp['network.tf']
        self.assertIn('resource "azurerm_resource_group" "main" {\n  name     = "rg-customer-portal-prod"\n'
                      '  location = "francecentral"', n)
        for label, cidr in (('apps', '10.0.0.0/24'), ('db', '10.0.1.0/24'), ('endpoints', '10.0.2.0/24')):
            self.assertIn(f'resource "azurerm_subnet" "{label}"', n)
            self.assertIn(f'address_prefixes                = ["{cidr}"]', n)
            self.assertIn(f'resource "azurerm_subnet_network_security_group_association" "{label}"', n)
        self.assertEqual(n.count('default_outbound_access_enabled = false'), 3)
        self.assertIn('name    = "Microsoft.App/environments"', n)
        self.assertIn('name    = "Microsoft.DBforPostgreSQL/flexibleServers"', n)
        self.assertIn('resource "azurerm_subnet_nat_gateway_association" "apps"', n)
        a = self.archive['network.tf']
        self.assertNotIn('azurerm_nat_gateway', a)                 # no app: no NAT, no apps subnet
        self.assertNotIn('"apps"', a)

    def test_nsg_rules(self):
        n = self.cp['network.tf']
        self.assertIn('name                       = "allow-https-from-internet"', n)
        self.assertIn('source_address_prefix      = "Internet"', n)
        self.assertIn('name                       = "allow-pg-from-apps"', n)
        self.assertIn('source_address_prefix      = "10.0.0.0/24"', n)
        self.assertIn('name                       = "deny-pg-from-vnet"', n)
        self.assertNotIn('allow-https-from-internet', self.tools['network.tf'])   # nothing public

    def test_object_storage(self):   # azure-full storage section + azurerm 5 forms
        s = self.cp['storage.tf']
        for needle in ('name                            = "stcustomeruploads${random_string.suffix.result}"',
                       'account_replication_type        = "ZRS"', 'min_tls_version                 = "TLS1_2"',
                       'shared_access_key_enabled       = false', 'public_network_access           = "Disabled"',
                       'versioning_enabled = true', 'storage_account_id    = azurerm_storage_account.uploads.id',
                       'subresource_names              = ["blob"]', 'name                = "privatelink.blob.core.windows.net"',
                       'lifecycle {\n    prevent_destroy = true\n  }'):
            self.assertIn(needle, s)
        t = self.tools['storage.tf']
        self.assertIn('account_replication_type        = "LRS"', t)          # westus has no zones
        self.assertNotIn('versioning_enabled', t)

    def test_postgres_password_is_ephemeral_and_write_only(self):   # probe: ephemeral random_password → *_wo
        d = self.cp['database.tf']
        self.assertIn('ephemeral "random_password" "db" {', d)
        self.assertIn('administrator_password_wo         = ephemeral.random_password.db.result\n'
                      '  administrator_password_wo_version = 1', d)
        self.assertIn('value_wo         = ephemeral.random_password.db.result', d)
        for needle in ('sku_name                          = "B_Standard_B1ms"', 'storage_mb                        = 32768',
                       'public_network_access_enabled     = false', 'zone                              = "1"',
                       'password_auth_enabled         = true', 'name                = "customer-portal-db.private.postgres.database.azure.com"'):
            self.assertIn(needle, d)
        self.assertNotIn('high_availability', d)
        t = self.tools['database.tf']
        self.assertIn('mode = "SameZone"', t)                                 # HA without zones
        self.assertNotIn('zone                              = "1"', t)
        self.assertIn('sku_name                          = "GP_Standard_D2ds_v5"', t)
        self.assertIn('mode = "ZoneRedundant"', self.shop['database.tf'])

    def test_one_key_vault_and_deployer_role(self):   # azure-full secrets section
        k = self.cp['secrets.tf']
        self.assertEqual(k.count('resource "azurerm_key_vault" '), 1)
        for needle in ('rbac_authorization_enabled = true', 'purge_protection_enabled   = true',
                       'role_definition_name = "Key Vault Secrets Officer"',
                       'value_wo         = var.payment_api_key', 'depends_on       = [azurerm_role_assignment.deployer_secrets]'):
            self.assertIn(needle, k)
        self.assertIn('secrets.tf', self.tools)                               # postgres alone needs the vault

    def test_container_apps(self):   # azure-full container app section
        a = self.cp['app.tf']
        for needle in ('internal_load_balancer_enabled = false', 'workload_profile_type = "Consumption"',
                       'external_enabled           = true', 'allow_insecure_connections = false',
                       'min_replicas = 2', 'cpu    = 0.5', 'memory = "1Gi"', 'image  = var.container_image',
                       'key_vault_secret_id = azurerm_key_vault_secret.payment_api_key.versionless_id',
                       'name        = "DB_PASSWORD"\n        secret_name = "db-password"',
                       'name  = "AZURE_CLIENT_ID"'):
            self.assertIn(needle, a)
        self.assertIn('internal_load_balancer_enabled = true', self.tools['app.tf'])   # no public app
        m = self.mixed['app.tf']
        self.assertIn('internal_load_balancer_enabled = false', m)
        self.assertEqual(m.count('external_enabled           = false'), 1)            # pricing: apps-only
        self.assertIn('reachable only from the other apps', self.mixed['README.md'])

    def test_edges_are_scoped_role_assignments(self):
        x = self.cp['access.tf']
        self.assertIn('resource "azurerm_role_assignment" "web_uploads" {\n'
                      '  scope                = azurerm_storage_account.uploads.id\n'
                      '  role_definition_name = "Storage Blob Data Contributor"', x)
        self.assertIn('scope                = azurerm_key_vault_secret.payment_api_key.resource_versionless_id', x)
        self.assertIn('scope                = azurerm_key_vault_secret.db_password.resource_versionless_id', x)
        self.assertIn('role_definition_name = "Storage Blob Data Reader"', self.tools['access.tf'])

    def test_globally_unique_names_fit(self):
        long = fixture('customer-portal.json') | {'name': 'a-very-long-project-name'}
        s = azure_files(long)
        self.assertIn('name                            = "staverylonuploads${random_string.suffix.result}"', s['storage.tf'])
        self.assertIn('name                       = "kv-a-very-long-pr-${random_string.suffix.result}"', s['secrets.tf'])

    def test_postgres_server_name_fits_63_characters(self):   # flexible server names are 3-63 lowercase, digits, hyphens
        import re
        raw = {'name': 'a' * 24, 'title': 'T', 'environment': 'e' * 10, 'target': {'cloud': 'azure', 'region': 'francecentral'},
               'components': [{'type': 'postgres', 'name': 'd' * 10 + '_' + 'e' * 9}]}
        d = azure_files(raw)['database.tf']
        m = re.search(r'resource "azurerm_postgresql_flexible_server" "\w+" \{\n\s+name\s+= "([^"]*)"', d)
        self.assertTrue(m, d)
        name = m.group(1).replace('${random_string.suffix.result}', 'x' * 6)
        self.assertLessEqual(len(name), 63, name)
        self.assertRegex(name, r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?$')
        self.assertNotIn('--', name)
        self.assertTrue(m.group(1).endswith('${random_string.suffix.result}'))

    def test_server_waits_for_the_vault_secret_and_readme_says_how_to_recover(self):
        d = self.cp['database.tf']
        self.assertIn('azurerm_key_vault_secret.db_password]', d.split('azurerm_postgresql_flexible_server" "db"')[1].split('lifecycle')[0])
        self.assertNotIn('azurerm_postgresql_flexible_server', d.split('resource "azurerm_key_vault_secret" "db_password"')[1])   # no cycle
        r = self.cp['README.md']
        self.assertIn('`administrator_password_wo_version`', r)
        self.assertIn('`value_wo_version`', r)
        self.assertIn('role assignment propagates', r)

    def test_readme_ha_wording_follows_the_mode(self):
        self.assertIn('zone-redundant standby', self.shop['README.md'])      # eastus has zones
        self.assertNotIn('same-zone standby', self.shop['README.md'])
        self.assertIn('same-zone standby', self.tools['README.md'])           # westus has none
        self.assertNotIn('zone-redundant standby', self.tools['README.md'])

    def test_readme_does_not_claim_the_endpoints_nsg_protects_endpoints(self):
        r = self.cp['README.md']
        self.assertNotIn('an NSG on each', r)
        self.assertIn("the endpoints subnet's NSG does not filter private-endpoint traffic", r)
        self.assertNotIn('has none', r)

    def test_two_databases_with_long_names_get_distinct_server_names(self):
        import re
        raw = {'name': 'a' * 24, 'title': 'T', 'environment': 'e' * 10, 'target': {'cloud': 'azure', 'region': 'francecentral'},
               'components': [{'type': 'postgres', 'name': 'orders_primary_a'}, {'type': 'postgres', 'name': 'orders_primary_b'}]}
        d = azure_files(raw)['database.tf']
        names = re.findall(r'resource "azurerm_postgresql_flexible_server" "\w+" \{\n\s+name\s+= "([^"]*)"', d)
        self.assertEqual(len(names), 2)
        self.assertNotEqual(names[0], names[1])
        for n in names:
            n = n.replace('${random_string.suffix.result}', 'x' * 6)
            self.assertLessEqual(len(n), 63)
            self.assertNotIn('--', n)

    def test_recovery_text_is_readme_only_not_a_warning(self):
        p = create.AzureProject(spec_of(fixture('customer-portal.json'), 'azure'))
        p.files()
        self.assertFalse([w for w in p.warnings if 'administrator_password_wo_version' in w])
        self.assertIn('## If an apply fails', p.readme())
        self.assertNotIn('## If an apply fails', self.archive['README.md'])   # no database, no paragraph


if __name__ == '__main__':
    unittest.main()
