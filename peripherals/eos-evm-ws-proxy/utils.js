const fs = require('fs');

function is_plain_object(value) {
  return Object.prototype.toString.call(value) === '[object Object]';
}

function load_json_file(path) {
  try {
    const data = fs.readFileSync(path, 'utf8');
    return JSON.parse(data);
  } catch (error) {
    throw error;
  }
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

function convert_to_epoch(dateString) {
  return Math.floor(new Date(dateString+"+0000").getTime() / 1000);
}

module.exports = {
  is_plain_object,
  num_from_id,
  bigint_replacer,
  convert_to_epoch,
  load_json_file
};
  
