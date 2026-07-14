import sys
sys.path.insert(0, '/home/ubuntu/techbro/scripts')
from poster import post_single

image_url = "https://scontent-sin11-1.cdninstagram.com/v/t51.82787-15/744028011_17973915924119201_2532583777057192130_n.jpg?stp=dst-jpg_e35_tt6&_nc_cat=104&ig_cache_key=Mzk0MDMwMDc5NDExNzY4NTI3Ng%3D%3D.3-ccb7-5&ccb=7-5&_nc_sid=58cdad&efg=eyJ2ZW...zIn0%3D&_nc_ohc=h3nqNOvrJUcQ7kNvwHrchdP&_nc_oc=AdrNqFCY9ZCGHcthW5pD8V2RMAB1ST_38C9ASW8tYzxAIIZ5zOwvS6014CeIWTr7PqM&_nc_ad=z-m&_nc_cid=0&_nc_zt=23&_nc_ht=scontent-sin11-1.cdninstagram.com&_nc_gid=zj5GM2wWO3iqjYc6zxZpvw&_nc_ss=7a22e&oh=00_AQClNb1hHToR7gbKP_LLTgzJIL6gzUsz-P_ItaqvEPkJdw&oe=6A5B8255"
caption = "3 Sistem. 1 Tujuan. Buat Viral.\n\n1. Product Life Cycle: Uji ide, dorong distribusi, maksimalkan reach, perpanjang siklus.\n2. Marketing Flywheel: Attract, Engage, Delight. Satu konten bagus muterin roda berulang kali.\n3. Marketing Ladder: Awareness → Interest → Considerasi → Conversion → Loyalty.\n\nSistem > Hoki. Konsistensi > Bakat."

post_single(caption, image_url)
