import sys
sys.path.insert(0, '/home/ubuntu/techbro/scripts')
from poster import post_single
import httpx
from bs4 import BeautifulSoup

# Fetch the actual image URL from the Threads post
url = "https://www.threads.com/@ryanhadiii/post/DauwrIVkwAc"
try:
    r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"})
    soup = BeautifulSoup(r.text, "html.parser")
    og_image = soup.find("meta", property="og:image")
    if og_image:
        image_url = og_image["content"]
        print(f"Found image URL: {image_url}")
        
        # Post the user's image with a caption (condensed to <500 chars)
        caption = "3 Sistem. 1 Tujuan. Buat Viral.\n\n1. Product Life Cycle: Uji ide, dorong distribusi, maksimalkan reach, perpanjang siklus.\n2. Marketing Flywheel: Attract, Engage, Delight. Satu konten bagus muterin roda berulang kali.\n3. Marketing Ladder: Awareness → Interest → Considerasi → Conversion → Loyalty.\n\nSistem > Hoki. Konsistensi > Bakat."

        post_single(caption, image_url)
    else:
        print("No og:image found")
except Exception as e:
    print(f"Error: {e}")
