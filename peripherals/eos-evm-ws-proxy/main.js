const config = require('./config');
const {logger} = require('./logger');
const SubscriptionServer = require('./subscription-server');

try {
    const server = new SubscriptionServer({...config, logger});
    server.start();
} catch (error) {
    logger.error(`main: ${error.message}`);
}