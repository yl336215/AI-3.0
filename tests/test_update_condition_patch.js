const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const context = vm.createContext({
  URLSearchParams,
  localStorage: { getItem: () => null },
  window: { location: { search: '' } },
  document: { addEventListener: () => {} },
});
vm.runInContext(
  fs.readFileSync(path.join(__dirname, '../web/ui/app.js'), 'utf8'),
  context,
);

const inputs = [
  { dataset: { condition: 'line' }, value: 'epump2', type: 'text' },
  { dataset: { condition: 'reference' }, value: 'old', type: 'text' },
];
const root = {
  querySelectorAll: selector => selector === '[data-condition]' ? inputs : [],
};
context.root = root;
vm.runInContext('state.updateDialog.conditionBaseline = collectConditions(root, true)', context);
assert.deepEqual(JSON.parse(JSON.stringify(vm.runInContext('collectChangedConditions(root)', context))), {});

inputs[1].value = 'new';
assert.deepEqual(
  JSON.parse(JSON.stringify(vm.runInContext('collectChangedConditions(root)', context))),
  { reference: 'new' },
);

inputs[1].value = '';
assert.deepEqual(
  JSON.parse(JSON.stringify(vm.runInContext('collectChangedConditions(root)', context))),
  { reference: null },
);
