To deploy you need:
- Api key
- PostgresPassword
Both can be aquired through repository owner

To run simply write
# docker-compose up --build -d

To shut down and build after synch:
# docker-compose down -v
# docker-compose up -d --build
# docker-compose cp API/CSUBotAPI/DbScripts/init.sql db:/tmp/init.sql
# docker-compose exec db psql -U CSUBotDB_user -d CSUBotDB -f /tmp/init.sql

To check if all work properly:
# docker-compose exec db psql -U CSUBotDB_user -d CSUBotDB
# \d words
# SELECT * FROM words;