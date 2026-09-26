// User lookup helpers (Node script)
const USERS = [
  { id: 1, name: 'Alice Johnson', active: true },
  { id: 2, name: 'Bob Smith', active: true },
  { id: 3, name: 'Carol White', active: false },
];

function findUser(id) {
  const user = USERS.find((u) => u.id === id);
  return user;
}

module.exports = { findUser, USERS };
