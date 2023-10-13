const config = require('./config');
const {logger} = require('./logger');
const SubscriptionServer = require('./subscription-server');
const server = new SubscriptionServer({...config, logger});
server.start();