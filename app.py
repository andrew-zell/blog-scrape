from flask import Flask, jsonify, request, send_file, render_template, session
import requests
import os
import json
from bs4 import BeautifulSoup
from bs4.element import Tag
from xml.etree import ElementTree as ET
from PIL import Image
from io import BytesIO
import re
from urllib.parse import urljoin
from functools import wraps

app = Flask(__name__)
# Use environment variables for secrets; set these in your environment for production!
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'changeme-please')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'changeme123')
DATA_FILE = 'data.json'
RSS_SOURCE = 'zoom_blog_feed_v3.xml'  # Or a URL if you want live fetching

# --- Admin session decorator ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin'):
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin_login', methods=['POST'])
def admin_login():
    data = request.get_json()
    if data.get('password') == ADMIN_PASSWORD:
        session['admin'] = True
        return jsonify({'success': True})
    return jsonify({'success': False}), 401

@app.route('/admin_logout', methods=['POST'])
def admin_logout():
    session.pop('admin', None)
    return jsonify({'success': True})

def parse_rss():
    # Load RSS from file or URL
    if RSS_SOURCE.startswith('http'):
        resp = requests.get(RSS_SOURCE)
        xml = resp.text
    else:
        with open(RSS_SOURCE, 'r', encoding='utf-8') as f:
            xml = f.read()
    root = ET.fromstring(xml)
    channel = root.find('channel')
    posts = []
    for item in channel.findall('item'):
        title = item.find('title').text
        guid = item.find('guid').text
        description = item.find('description').text
        soup = BeautifulSoup(description, 'html.parser')
        blocks = soup.find_all('div')
        images = []
        for block in blocks:
            img = block.find('img')
            caption = block.find('p')
            if img and caption:
                images.append({
                    'src': img['src'],
                    'caption': caption.text,
                    'include': True
                })
        posts.append({
            'id': guid,
            'title': title,
            'images': images,
            'include': True
        })
    return posts

def load_data():
    if not os.path.exists(DATA_FILE):
        posts = parse_rss()
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(posts, f, indent=2)
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def scrape_zoom_blog():
    url = "https://www.zoom.com/en/blog/"
    resp = requests.get(url)
    soup = BeautifulSoup(resp.text or '', "html.parser")
    posts = []
    for article in soup.select("article"):
        title_tag = article.find("h2") or article.find("h3")
        title = title_tag.get_text(strip=True) if title_tag else "No Title"
        link_tag = article.find("a", href=True)
        story_url = "https://www.zoom.com" + link_tag['href'] if link_tag else None
        images = []
        # Fetch the full story page for more images/captions
        if story_url:
            story_resp = requests.get(story_url)
            story_soup = BeautifulSoup(story_resp.text, "html.parser")
            for img_tag in story_soup.select("img"):
                img_src = img_tag['src'] if img_tag.has_attr('src') else ""
                # Skip if src is empty or looks like an icon/logo
                if not img_src or any(x in img_src for x in ['icon', 'logo', 'avatar', 'sprite']):
                    continue
                # Try to filter by size (if width/height attributes are present)
                width = int(img_tag.get('width', 0))
                height = int(img_tag.get('height', 0))
                if (width and width < 500) and (height and height < 500):
                    continue
                # Caption: alt text, or nearby figcaption/p
                caption = ""
                if img_tag.has_attr('alt') and img_tag['alt'].strip():
                    caption = img_tag['alt'].strip()
                else:
                    caption_tag = img_tag.find_next("figcaption") or img_tag.find_next("p")
                    if caption_tag:
                        caption = caption_tag.get_text(strip=True)
                images.append({
                    "src": img_src,
                    "caption": caption,
                    "include": True
                })
        # Fallback: if no images found, use the thumbnail
        if not images:
            img_tag = article.find("img")
            img_src = img_tag['src'] if img_tag and img_tag.has_attr('src') else ""
            caption = ""
            if img_tag and img_tag.has_attr('alt') and img_tag['alt'].strip():
                caption = img_tag['alt'].strip()
            images.append({
                "src": img_src,
                "caption": caption,
                "include": True
            })
        posts.append({
            "id": story_url or title,
            "title": title,
            "images": images,
            "include": False  # New articles are not included by default
        })
    return posts

def add_new_articles():
    data = load_data()
    existing_ids = {post['id'] for post in data}
    new_posts = scrape_zoom_blog()
    added = False
    for post in new_posts:
        if post['id'] not in existing_ids:
            data.append(post)
            added = True
    if added:
        save_data(data)

@app.route('/scrape', methods=['POST'])
def manual_scrape():
    data = load_data()
    existing_ids = {post['id'] for post in data}
    new_posts = scrape_zoom_blog()
    added = False
    for post in new_posts:
        if post['id'] not in existing_ids:
            data.append(post)
            added = True
    if added:
        save_data(data)
    return jsonify({'status': 'scraped', 'added': added})

@app.route('/api/posts', methods=['GET'])
@admin_required
def get_posts():
    return jsonify(load_data())

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/posts/<path:post_id>', methods=['POST'])
@admin_required
def update_post(post_id):
    data = load_data()
    for post in data:
        if post['id'] == post_id:
            post.update(request.json)
            # Ensure title is updated if provided
            if 'title' in request.json:
                post['title'] = request.json['title']
            print(f"Updated post: {json.dumps(post, indent=2)}")  # Debug print
            break
    save_data(data)
    return jsonify({'status': 'ok'})

@app.route('/api/posts', methods=['POST'])
@admin_required
def save_all_posts():
    data = request.json
    save_data(data)
    print("Saved all posts (Save All):")
    print(json.dumps(data, indent=2))
    return jsonify({'status': 'ok'})

@app.route('/feed.xml')
def feed():
    data = load_data()
    print("Generating feed with data:")
    print(json.dumps(data, indent=2))  # Debug print
    # Generate RSS from curated data
    rss = ET.Element('rss', version='2.0')
    channel = ET.SubElement(rss, 'channel')
    ET.SubElement(channel, 'title').text = 'Zoom Blog Visual Feed (Curated)'
    ET.SubElement(channel, 'link').text = 'https://zoom.com/en/blog'
    ET.SubElement(channel, 'description').text = 'Curated blog visuals'
    for post in data:
        if not post.get('include', True):
            continue
        item = ET.SubElement(channel, 'item')
        ET.SubElement(item, 'title').text = post['title']
        ET.SubElement(item, 'guid').text = post['id']
        desc = ''
        for img in post['images']:
            if img.get('include', True):
                desc += f'<div style="margin-bottom:30px;"><img src="{img["src"]}" style="width:100%;max-width:1000px;"><p>{img["caption"]}</p></div>'
        ET.SubElement(item, 'description').text = f'<![CDATA[<div>{desc}</div>]]>'
    xml_str = ET.tostring(rss, encoding='utf-8')
    return app.response_class(xml_str, mimetype='application/xml')

def is_valid_image(src):
    return src.lower().endswith(('.jpg', '.jpeg', '.png', '.gif'))

def get_image_size(src):
    try:
        resp = requests.get(src, timeout=5)
        img = Image.open(BytesIO(resp.content))
        return img.size  # (width, height)
    except Exception:
        return (0, 0)

def get_img_src(img, base_url):
    for attr in ['src', 'data-src', 'data-original', 'data-lazy']:
        src = img.get(attr)
        if not src:
            continue
        if isinstance(src, list):
            src = src[0]
        # Handle relative URLs
        if src.startswith('/'):
            src = urljoin(base_url, src)
        if src.startswith('http'):
            return src
    return None

@app.route('/scrape_story', methods=['POST'])
@admin_required
def scrape_story():
    data = request.get_json()
    story_url = data.get('url')
    if not story_url:
        return jsonify({'error': 'No URL provided'}), 400
    try:
        res = requests.get(story_url)
        soup = BeautifulSoup(res.text, 'html.parser')
        images = []
        for img in soup.find_all("img"):
            src = img.get("src")
            if not src or not src.startswith("http"):
                continue
            # Look for nearby caption: figcaption, em, or p
            caption = ""
            next_tag = img.find_next_sibling()
            if next_tag and next_tag.name in ["figcaption", "em", "p"]:
                caption = next_tag.get_text(strip=True)
            # Only keep meaningful ones
            if caption:
                images.append({"src": src, "caption": caption, "include": True})
        return jsonify({
            'title': soup.title.string if soup.title else story_url,
            'url': story_url,
            'images': images
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True) 
