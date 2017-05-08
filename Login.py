from PIL import Image
import mechanize,cookielib
from mechanize import Browser
from BeautifulSoup import BeautifulSoup
from StringIO import StringIO
from Captcha_Parser import CaptchaParse
import ssl
import re
import requests
import httplib2
import datetime
def calsem():
        now = datetime.datetime.now()
        if int(now.month)>6 and int(now.month)<13:
                sem="FS"
        else:
                sem="WS"
        return sem

br= mechanize.Browser()
br.set_handle_equiv(True)
br.set_handle_redirect(True)
br.set_handle_referer(True)
br.set_handle_robots(False)


try:
        _create_unverified_https_context=ssl._create_unverified_context
except AttributeError:
        pass
else:
        ssl._create_default_https_context=_create_unverified_https_context       

r=br.open('https://vtop.vit.ac.in/student/stud_login.asp')
html=r.read()
soup=BeautifulSoup(html)
im = soup.find('img', id='imgCaptcha')
image_response = br.open_novisit(im['src'])
img=Image.open(StringIO(image_response.read()))
captcha=CaptchaParse(img)
print "Recognized Captcha:"+str(captcha)
br.select_form('stud_login')
regno=raw_input("Registration Number:")
password=raw_input("Password:")
sem=calsem()
br.form['regno']=regno
br.form['vrfcd']=str(captcha)
br.form['passwd']=password
print "Logging in User:"
response=br.submit()
"""
print "*********response******"
print response.info()
print "**********************"
"""
cookies = br._ua_handlers['_cookies'].cookiejar
# convert cookies into a dict usable by requests
cookie_dict = {}
for c in cookies:
    cookie_dict[c.name] = c.value
#cookie_dict has the current session cookie
if(response.geturl()=="https://vtop.vit.ac.in/student/home.asp"):
        print "Successfully Logged In"
else:
        print "Recheck Credentials"

