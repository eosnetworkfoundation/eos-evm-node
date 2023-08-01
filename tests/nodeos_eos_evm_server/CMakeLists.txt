add_subdirectory( contracts )
add_subdirectory( scripts )

configure_file( extract-logtime-cmd.sh . COPYONLY )
configure_file( hardhat.config.js . COPYONLY )
configure_file( package.json . COPYONLY )
configure_file( yarn.lock . COPYONLY )