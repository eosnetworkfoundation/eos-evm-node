function is_plain_object(value) {
  return Object.prototype.toString.call(value) === '[object Object]';
}

function num_from_id(id) {
  if( id.startsWith('0x') ) {
    id = id.substring(2);
  }
  return parseInt(id.slice(0, 8), 16);
}

function bigint_replacer(key, value) {
  if (typeof value === 'bigint') {
    return value.toString();
  }
  return value;
}

module.exports = {
  is_plain_object,
  num_from_id,
  bigint_replacer
};
  