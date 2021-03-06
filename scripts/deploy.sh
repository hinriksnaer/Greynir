#! /bin/bash
#
# deploy.sh
#
# Deployment script for greynir.is
# 
# Prompts for confirmation before copying files over
#
# Defaults to deploying to production.
# Run with argument "staging" to deploy to staging

SRC=~/github/Greynir
MODE="PRODUCTION"
DEST=/usr/share/nginx/greynir.is # Production
SERVICE="greynir"

if [ "$1" = "staging" ]; then
    MODE="STAGING"
    DEST=/usr/share/nginx/staging # Staging
    SERVICE="staging"
fi

read -p "This will deploy Greynir to **${MODE}**. Confirm? (y/n): " CONFIRMED

if [ "$CONFIRMED" != "y" ]; then
    echo "Deployment aborted"
    exit 1
fi

echo "Deploying $SRC to $DEST..."

echo "Stopping gunicorn server"

sudo systemctl stop $SERVICE

cd $DEST

# echo "Upgrading the Greynir package"

# source p369/bin/activate
# pip install --upgrade -r requirements.txt
# deactivate


echo "Removing binary grammar files"
rm p369/site-packages/reynir/Reynir.grammar.bin
rm p369/site-packages/reynir/Reynir.grammar.query.bin


cd $SRC

echo "Copying files"

cp config/Adjectives.conf $DEST/config/Adjectives.conf
cp config/Index.conf $DEST/config/Index.conf
# Note: config/Greynir.conf is not copied
cp config/TnT-model.pickle $DEST/config/TnT-model.pickle

cp article.py $DEST/article.py
cp correct.py $DEST/correct.py
cp fetcher.py $DEST/fetcher.py
cp geo.py $DEST/geo.py
cp images.py $DEST/images.py
cp main.py $DEST/main.py
cp nertokenizer.py $DEST/nertokenizer.py
cp postagger.py $DEST/postagger.py
cp processor.py $DEST/processor.py
cp query.py $DEST/query.py
cp scraper.py $DEST/scraper.py
cp doc.py $DEST/doc.py
cp -r db $DEST/
cp -r routes $DEST/
cp search.py $DEST/search.py
cp settings.py $DEST/settings.py
cp similar.py $DEST/similar.py
cp speech.py $DEST/speech.py
cp tnttagger.py $DEST/tnttagger.py
cp tree.py $DEST/tree.py
cp treeutil.py $DEST/treeutil.py
cp scrapers/*.py $DEST/scrapers/
cp queries/*.py $DEST/queries/
cp nn/*.py $DEST/nn/

# Processors are not required for the web server
# cp processors/*.py $DEST/processors/

# Sync templates and static files
rsync -av --delete templates/ $DEST/templates/
rsync -av --delete static/ $DEST/static/

cp resources/*.json $DEST/resources/

# Put a version identifier (date and time) into the about.html template
# TODO: Put Git commit hash / revision count here as well as date and time
sed -i "s/\[Þróunarútgáfa\]/Útgáfa `date "+%Y-%m-%d %H:%M"`/g" $DEST/templates/about.html
sed -i "s/\[Gitútgáfa\]/`git rev-parse HEAD`/g" $DEST/templates/about.html

echo "Deployment done"
echo "Starting gunicorn server..."

sudo systemctl start $SERVICE
