// The publish dialog's generated pull-request title (issue #25).
//
// No framework: `node --test tests/publish-title.test.js`.

const test = require('node:test');
const assert = require('node:assert/strict');

const { titleFor } = require('../static/js/publish-title.js');

test('a title says what the submission does', () => {
  assert.equal(titleFor([{ name: "Addison's disease", is_new: false }]), "Update Addison's disease");
  assert.equal(titleFor([{ name: 'Brand new', is_new: true }]), 'Add Brand new');
  assert.equal(titleFor([]), 'Update ontology');
});

test('two diseases are named and three are counted', () => {
  const two = [{ name: 'A', is_new: false }, { name: 'B', is_new: false }];
  assert.equal(titleFor(two), 'Update A and B');
  assert.equal(titleFor([...two, { name: 'C', is_new: false }]), 'Update 3 diseases');
});

test('added and updated are reported separately', () => {
  const mixed = [{ name: 'New one', is_new: true },
                 { name: 'A', is_new: false }, { name: 'B', is_new: false }];
  assert.equal(titleFor(mixed), 'Add New one; update A and B');
});
