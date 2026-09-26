// Usage: node scripts/greet.js <id>
const { findUser } = require('./users');

function greeting(id) {
  const user = findUser(id);
  return `Hello, ${user.name}!`;
}

function main(argv) {
  const ids = argv.length ? argv.map(Number) : [1, 2, 3];
  for (const id of ids) {
    console.log(greeting(id));
  }
}

main(process.argv.slice(2));
