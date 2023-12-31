from django import template
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseRedirect
from django.template import loader
from django.urls import reverse
from time import sleep
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import NoSuchElementException
from python_anticaptcha import AnticaptchaClient, NoCaptchaTaskProxylessTask
from selenium.webdriver.common.action_chains import ActionChains
from datetime import datetime, timedelta
from urllib.request import urlretrieve
import urllib.error
import re
import pytesseract
import os
from pdf2image import convert_from_path
from undetected_chromedriver import Chrome, ChromeOptions
import airtable
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
import cv2
import io
import numpy as np
import psutil
import logging
import pytz
import concurrent.futures
from .models import Lead, CronJobs
from urllib.parse import unquote
import fitz


logging.basicConfig(
    filename='log.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(funcName)s - %(message)s'
)
logger = logging.getLogger(__name__)


api_key = 'e77cab79ca105a72b529f7b0026b7ee1'
url = 'https://iapps.courts.state.ny.us/nyscef/CaseSearch?TAB=courtDateRange'


def index(request):
    context = {'segment': 'index'}
    context['records'] = []
    leads = Lead.objects.all()
    context['records'] = leads
    html_template = loader.get_template('home/index.html')
    return HttpResponse(html_template.render(context, request))


def pages(request):
    context = {}
    # All resource paths end in .html.
    # Pick out the html file name from the url. And load that template.
    load_template = request.path.split('/')[-1]

    if load_template == 'admin':
        return HttpResponseRedirect(reverse('admin:index'))
    context['segment'] = load_template

    html_template = loader.get_template('home/index.html')
    return HttpResponse(html_template.render(context, request))
    
    
def download_file(download_url):
    try:
        file_name = download_url['name'] + '.pdf'
        file_name = file_name.replace('/','-').replace(':', '')
        contentName = download_url['contentName'].replace('/','-')
        if not os.path.exists('pdfs/' + contentName):
                    os.makedirs('pdfs/' + contentName)
                    
        if not os.path.exists('pdfs/' + contentName + '/' + file_name):
            urlretrieve(download_url['href'], 'pdfs/' + contentName + '/' + file_name)
            print(f"File downloaded successfully: {file_name}")
    except urllib.error.URLError as e:
        print(f"Error downloading file: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

            
circles = []
counter = 0
counter2 = 0
Clickpoint1 = []
Clickpoint2 = []
myCoordinates = []   
img = None
def mouseClickPoints(event, x, y, flags, params):
    global counter, Clickpoint1, Clickpoint2, counter2, circles
    if event == cv2.EVENT_LBUTTONDOWN:
        # Draw circle in red color
        cv2.circle(img, (x, y), 3, (0, 0, 255), cv2.FILLED)
        if counter == 0:
            Clickpoint1 = int(x), int(y)
            counter += 1
        elif counter == 1:
            Clickpoint2 = int(x), int(y)
            if counter2 == 0:
                myCoordinates.append([Clickpoint1, Clickpoint2])
            else:
                myCoordinates[-1][1] = Clickpoint2  # Update bottom-right corner
            counter = 0
            circles.append([x, y])
            counter2 += 1
            if counter2 == 2:  # Capture all four corners
                counter2 = 0
            
def choose_location(image):
    global img
    # Convert image to bytes
    img_bytes = io.BytesIO()
    image.save(img_bytes, format='JPEG')  # Save as JPEG (or other supported format)
    img_bytes = img_bytes.getvalue()

    # Decode the image bytes using OpenCV
    img = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), 1)
    height, width, channel = img.shape
    img = cv2.resize(img, (width//2, height//2))
    # Function to store left-mouse click coordinates
    
    while True:
        # To Display clicked points
        for x, y in circles:
            cv2.circle(img, (x, y), 3, (0,0,255), cv2.FILLED)
        # Display original image
        cv2.imshow('Original Image', img)
        # Collect coordinates of mouse click points
        cv2.setMouseCallback('Original Image', mouseClickPoints)
        # Press 'x' in keyboard to stop the program and print the coordinate values
        if cv2.waitKey(1) & 0xFF == ord('x'):
            print(myCoordinates)
            break         

def download_pdfs(link,records):

    optionsUC = webdriver.ChromeOptions()
    optionsUC.add_argument('--window-size=720,1280')
    optionsUC.add_argument('--no-sandbox')
    optionsUC.add_argument('--headless')
    optionsUC.add_argument('--disable-dev-shm-usage')
    optionsUC.add_argument('start-maximized')
    elemntDriver = webdriver.Chrome(options=optionsUC)
    elemntDriver.get(link)

    docs = []

    docs_elements = elemntDriver.find_elements(
        By.CSS_SELECTOR, 'table.NewSearchResults > tbody > tr'
    )

    merchant_type = False
    for doc in docs_elements:
        if 'processed' not in doc.text.lower():
            continue
        
        if is_targeted_doc(doc.text.lower()):
            merchant_type = True
        
        if len(doc.find_elements(By.CSS_SELECTOR, 'td:nth-child(2) > a'))>0:
            docs.append({
                'link': doc.find_element(
                    By.CSS_SELECTOR, 'td:nth-child(2) > a'
                )
            })
    
    if not merchant_type:
        elemntDriver.quit()
        return

    contentName = elemntDriver.find_element(
        By.CSS_SELECTOR, '#row').get_attribute('value').strip()
    hrefs = []
    
    for doc in docs:
        hrefs.append({
            'href': doc['link'].get_attribute('href'),
            'name': doc['link'].get_attribute('innerHTML').strip(),
            'contentName': contentName.replace("/","-")
        })
    
    # NOTE: Isoloate business legal endings
    parties = get_case_detail(elemntDriver)

    types = [
        'summons',
        'petition',
        'exhibit',
        'statement of authorization',
        'complaint' 
    ]

    # need summons & exhibits
    hrefs = [h
        for h in hrefs 
        if any(t in h['name'].lower() 
        for t in types)
    ]
    if len(hrefs) > 1:
        count = 0
        failed = 0
        for href in hrefs:
            try:
                elemntDriver.get(href['href'])
                download_file(href)
                count += 1
                sleep(5)
            except Exception as e:
                failed += 1
                logger.error(e, exc_info=True)
                print(e)
            sleep(5)
        
        logger.info(
            f'Found {len(hrefs)} cases with exhibit files, ' \
            f'{count} successful downloads, '
            f'{failed} download failures'
        )
        if len(hrefs):
            extract_texts(contentName.replace("/","-"), parties, records)

    elemntDriver.quit()

def is_targeted_doc(doc_text: str) -> bool:
    '''Determines if the document matches necessary criteria'''
    exhibit_types = [
        'contract',
        'copy of merchant agreement',
        'revenue purchase agreement',
        'contract agreement',
        'contract redacted',
        'redacted contract',
        'frpa (future receivable purchase agreement)',
        'agreement',
        'merchant agreement',
        'purchase of future receivables'
    ]
    form_conditions = ['exhibit']

    if not all(substr in doc_text for substr in form_conditions):
        return False
    if any(substr in doc_text for substr in exhibit_types):
        return False
    return True

# def get_case_detail(driver) -> dict[str, list[dict]]:
def get_case_detail(driver):
    '''Gets case detail from the document page
    
    Returns:
        A dictionary with values as lists of party data dicts
    '''
    tab_xpath = '//div[@id="tabs"]//ul//li[2]/a'
    plaintiffs_table_xpath = '//div[@class="DataEntry_InnerBox"]//table[1]/tbody'
    defendants_table_xpath = '//div[@class="DataEntry_InnerBox"]//table[2]/tbody'

    detail_tab = driver.find_element(By.XPATH, tab_xpath)
    detail_tab.click()
    sleep(2)

    logger.info('Gathering plaintiffs')
    plaintiffs = []
    try:
        plaintiff_elems = driver.find_element(By.XPATH, plaintiffs_table_xpath)
    except Exception as e:
        plaintiff_elems = None
        logger.error(e, exc_info=True)
    if plaintiff_elems:
        for tr in plaintiff_elems.find_elements(By.TAG_NAME, 'tr'):
            columns = tr.find_elements(By.TAG_NAME, 'td')
            name, consented_by = columns[0].text, columns[1].text
            plaintiffs.append({'name': name, 'consented_by': consented_by})

    logger.info('Gathering defendants')
    defendants = []
    try:
        defendant_elems = driver.find_element(By.XPATH, defendants_table_xpath)
    except Exception as e:
        defendant_elems = None
        logger.error(e, exc_info=True)
    if defendant_elems:
        for tr in defendant_elems.find_elements(By.TAG_NAME, 'tr'):
            columns = tr.find_elements(By.TAG_NAME, 'td')
            name, consented_by = columns[0].text, columns[1].text
            defendants.append({'name': name, 'consented_by': consented_by})
        
    driver.back() # go back to links tab
    sleep(3)

    return {
        'plaintiffs': plaintiffs,
        'defendants': defendants
    }

# def parse_entities(party: list[dict]) -> tuple[list, str]:
def parse_entities(party):
    '''Parses out people and companies
    
    Parameters:
        party: List of dictionaries that contain 'name' and 'consented_by'

    Returns:
        A tuple of a list of people and a string of entities.
    '''
    people = []
    entities = ''

    for p in party:
        if is_entity(p['name']):
            entities += f"{p['name']}, "
        else:
            people.append(p['name'])
    return people, entities.strip(', ')

def is_entity(s: str) -> bool:
    # Enhance the basic heuristics to elimiate substrings in a person's name
    s = ' ' + s.replace('.', ' ') + ' '

    legal_entities = [
        " LLC ", " LLP ", " LP ", " Inc ", " Corp ", " Co ", " Ltd ", " PLLC ",
        " PC ", " DBA ", " Corp ", " Corp ", " RLLP ", " L3C ", " P.A. ", 
        " T/A ", " FZ-LLC ",  " Company ", " Board "
    ]

    for entity in legal_entities:
        if entity.lower() in s.lower():
            return True
    return False


def extract_texts(folder, case_detail: dict, records):
    folder_path='pdfs'
    files = [file for file in os.listdir(folder_path + '/' + folder) if file.endswith(".pdf")]
    record = {}
    
    record['folder'] = f'https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId={folder.replace("-","/")}&display=all'
    if folder != '':
        logger.info(f'Processing {len(files)} PDFs in the {folder} folder')
        for pdf_file in files:
            if 'complaint' in pdf_file.lower():
                pdf_path = os.path.join(folder_path + '/' + folder, pdf_file)
                numbers = []
                county = ''
                try:
                    # use the poppler_path argument in case there is an exception in running convert_from_path
                    # images = convert_from_path(pdf_path)
                    images = convert_from_path(pdf_path)
                    for i, image in enumerate(images):
                        text = pytesseract.image_to_string(image, lang='eng')
                        # Loop through each line in the text
                        for line in text.split('\n'):
                            # If county is not found yet, try to find and extract it from the line
                            if not county:  # equivalent to county == ''
                                county_match = re.search('FILED:([\s\S]*?)COUNTY', line)
                                if county_match:
                                    try:
                                        # Extract the county information from the match
                                        county = county_match.group(1).strip()
                                        record['county'] = county
                                    except IndexError as e:  # Handle specific exception
                                        logger.error(f'County extract error: {e}', exc_info=True)
                                        print(f'County extract error: {e}')

                            # Find and convert all monetary values in the line
                            for monetary_match in re.findall('\$\d+,\d+\.\d+|\$\d+,\d+|\$\d+', line):
                                try:
                                    # Clean the monetary value and convert it to float
                                    clean_number = monetary_match.replace('$', '').replace(',', '')
                                    numbers.append(float(clean_number))
                                except ValueError as e:  # Handle specific exception
                                    logger.error(f'Monetary value conversion error: {e}', exc_info=True)
                                    print(f'Monetary value conversion error: {e}')
                    if len(numbers)>0:
                        price = max(numbers)
                        record['price'] = price
                except Exception as e:
                    logger.error(f'Complaint extract error: {e}', exc_info=True)
                    print('Complaint extract error'+str(e))

        msg = str(record['price']) if 'price' in record else 'Price not found'
        print(msg)
        logger.info(msg)
        if 'price' in record and record['price'] >= 20000: 
            print(files)
            logger.info(files)
            for pdf_file in files:
                try:
                    pdf_path = os.path.join(folder_path + '/' + folder, pdf_file)
                    logger.info(pdf_path)
                    print(pdf_path)
                    if 'summons' in pdf_file.lower() or 'petition' in pdf_file.lower():
                        try:
                            images = convert_from_path(pdf_path, first_page=0, last_page=2)
                            for i, image in enumerate(images):
                                if i == 0 or i == 1:
                                    try:
                                        custom_config = r'--oem 3 --psm 6'
                                        #(left, upper, right, lower)
                                        cropped_image = image.crop((1, 100, 1000, 1000))
                                        text = pytesseract.image_to_string(cropped_image, lang='eng')
                                        creditor_name = ''
                                        company_sued = ''
                                        #(left, upper, right, lower)
                                        cropped_image = image.crop((1, 150, 1500, 1000))
                                        img_bytes = io.BytesIO()
                                        cropped_image.save(img_bytes, format='JPEG')  # Save as JPEG (or other supported format)
                                        img_bytes = img_bytes.getvalue()
                                        
                                        # # Decode the image bytes using OpenCV
                                        img = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
                                        kernel_size = 5
                                        blur_gray = cv2.GaussianBlur(img,(kernel_size, kernel_size),0)
                                        low_threshold = 50
                                        high_threshold = 150
                                        edges = cv2.Canny(blur_gray, low_threshold, high_threshold)
                                        rho = 1  # distance resolution in pixels of the Hough grid
                                        theta = np.pi / 180  # angular resolution in radians of the Hough grid
                                        threshold = 15  # minimum number of votes (intersections in Hough grid cell)
                                        min_line_length = 250  # minimum number of pixels making up a line
                                        max_line_gap = 5  # maximum gap in pixels between connectable line segments
                                        line_image = np.copy(img) * 0  # creating a blank to draw lines on

                                        # Run Hough on edge detected image
                                        # Output "lines" is an array containing endpoints of detected line segments
                                        lines = []
                                        lines = cv2.HoughLinesP(edges, rho, theta, threshold, np.array([]),
                                                            min_line_length, max_line_gap)
                                        image_height, image_width = img.shape[:2]
                                        upper_x1 = 0
                                        upper_y1 = 0
                                        if np.any(lines):
                                            for i,line in enumerate(lines):
                                                x1, y1, x2, y2 = line[0]
                                                slope = (y2 - y1) / (x2 - x1 + 1e-5)  # Calculate slope while avoiding division by zero
                                                angle = np.arctan(slope) * 180 / np.pi
                                                average_y = (y1 + y2) / 2
                                                try:
                                                    if abs(angle) < 10:
                                                        if average_y < image_height / 2:
                                                            for x1,y1,x2,y2 in line:
                                                                upper_x1 = x1
                                                                upper_y1 = y1 +70
                                                except Exception as e:
                                                    logger.error(f'Error passed: {e}', exc_info=True)
                                                    pass
                                                                
                                            
                                            for i,line in enumerate(lines):
                                                x1, y1, x2, y2 = line[0]
                                                slope = (y2 - y1) / (x2 - x1 + 1e-5)  # Calculate slope while avoiding division by zero
                                                angle = np.arctan(slope) * 180 / np.pi
                                                average_y = (y1 + y2) / 2
                                                try:
                                                    if abs(angle) < 10:
                                                        if average_y < image_height / 2:
                                                            for x1,y1,x2,y2 in line:
                                                                if creditor_name == '':
                                                                    try:
                                                                        text_region = img[y1:y2 + 400, x1:x2]
                                                                    except:
                                                                        try:
                                                                            text_region = img[y1:y2 + 300, x1:x2]
                                                                        except:
                                                                            text_region = img[y1:y2 + 200, x1:x2]
                                                                    
                                                                    box_test = pytesseract.image_to_string(text_region, config=custom_config).strip()
                                                                    print(re.findall('[\s\S]*?Plaintiff' ,box_test))
                                                                    logger.info(f"Box Test: " + str(re.findall('[\s\S]*?Plaintiff' ,box_test)))
                                                                    if(len(re.findall('[\s\S]*?Plaintiff' ,box_test))>0):
                                                                        creditor_name = re.findall('[\s\S]*?Plaintiff' ,box_test)[0].replace('Plaintiff','').strip()
                                                                    cv2.line(line_image,(x1,y1),(x2,y2),(255,0,0),5)
                                                                    # cv2.rectangle(img, (x1, y1), (x2, y2+400), (67, 255, 100), 2)
                                                        else:
                                                            for x1,y1,x2,y2 in line:
                                                                try:
                                                                    text_region = img[y1 - 500:y2 , x1:x2]
                                                                except:
                                                                    try:
                                                                        text_region = img[y1 - 400:y2 , x1:x2]
                                                                    except:
                                                                        text_region = img[y1 - 300:y2 , x1:x2]

                                                                box_test = pytesseract.image_to_string(text_region, config=custom_config).strip()
                                                                if(len(re.findall('against-\\n.*?,|against-\\n\\n.*?,|against-\\n\\n.*?;|-against-[\s\S]*Defendants' ,box_test))>0):
                                                                    company_sued = re.findall('against-\\n.*?,|against-\\n\\n.*?,|against-\\n\\n.*?;|-against-[\s\S]*Defendants' ,box_test)[0].replace('Defendants','').strip()
                                                                    if(len(re.findall('against-\\n|against-\\n\\n' ,company_sued))>0):
                                                                        company_sued = company_sued.replace(re.findall('against-\\n|against-\\n\\n' ,company_sued)[0], '').replace('-',company_sued,1)
                                                                if(len(re.findall('[\s\S]*?Plaintiff' ,box_test))>0 and creditor_name == ''):
                                                                        creditor_name = re.findall('[\s\S]*?Plaintiff' ,box_test)[0].replace('Plaintiff','').strip()

                                                                
                                                                cv2.line(line_image,(x1,y1),(x2,y2),(255,0,0),5)
                                                                cv2.rectangle(img, (upper_x1 if upper_x1 else x1, upper_y1 if upper_y1 else y1 - 500), (x2, y2), (67, 255, 100), 2)
                                                    else:
                                                        pass
                                                except Exception as e:
                                                    logger.error(f'creditor_name2 error: {e}', exc_info=True)
                                                    print('creditor_name2 error'+str(e))
                                            cv2.imwrite(folder_path+ "/" + folder + '/' + "text_under_line.jpg", img)
                                        
                                        if(len(re.findall('-against-\\n.*?,|-against-\\n\\n.*?,|-against-\\n\\n.*?;' ,text))>0) and company_sued == '':
                                            company_sued = re.findall('-against-\\n.*?,|-against-\\n\\n.*?,|-against-\\n\\n.*?;' ,text)[0]
                                            if(len(re.findall('-against-\\n|-against-\\n\\n' ,company_sued))>0):
                                                company_sued = company_sued.replace(re.findall('-against-\\n|-against-\\n\\n' ,company_sued)[0], '')

                                        if creditor_name != '':
                                            record['creditor_name'] = creditor_name.replace('Return To','').replace('Document Type: SUMMONS + COMPLAINT','')
                                        if company_sued != '':
                                            record['company_sued'] = company_sued
                                        images = convert_from_path(pdf_path,
                                                                first_page=0, last_page=2)
                                        # images = convert_from_path(pdf_path, poppler_path='/usr/local/Cellar/poppler/23.09.0/bin',
                                        #                            first_page=0, last_page=2)
                                        # Initialize the variables to hold defendant and plaintiff names
                                        defendant_name_1, defendant_name_2, plaintiff_name_1, plaintiff_name_2 = '', '', '', ''
                                        defendant_name = ''
                                        plaintiff_name = ''
                                        text = pytesseract.image_to_string(image, lang='eng')

                                        # Search for the first defendant name if it hasn't been found yet.
                                        if not defendant_name_1:
                                            # Define the pattern to find the first defendant name.
                                            # It is using MULTILINE mode to match the start of a line.
                                            defendant_pattern_1 = re.compile(r'^(.+),\s*\n?\n?Defendant', re.MULTILINE)
                                            defendant_match_1 = defendant_pattern_1.search(
                                                text)  # Perform the search to find the name

                                            if defendant_match_1:  # If a match is found
                                                # Extract and clean the defendant name from the text
                                                defendant_name_1 = defendant_match_1.group(1).strip()
                                                blocks = defendant_name_1.split('\n\n')
                                                defendant_name_1 = blocks[-1]  # Keep the last block
                                                defendant_name = defendant_name_1  # Assign to the final defendant_name variable

                                        # The process is similar for defendant_name_2, plaintiff_name_1, and plaintiff_name_2 with different patterns.
                                        if not defendant_name_2:
                                            defendant_pattern_2 = re.compile(r'([\s\S]+?)\n?\n?Defendant', re.MULTILINE)
                                            defendant_match_2 = defendant_pattern_2.search(text)

                                            if defendant_match_2:
                                                defendant_name_2 = defendant_match_2.group(1).strip()
                                                blocks = defendant_name_2.split('\n\n')
                                                defendant_name_2 = blocks[-1]
                                                if 'against' not in defendant_name_2:
                                                    defendant_name = defendant_name_2

                                        if not plaintiff_name_1:
                                            plaintiff_pattern_1 = re.compile(r'^(.+),\s*\n?\n?Plaintiff,', re.MULTILINE)
                                            plaintiff_match_1 = plaintiff_pattern_1.search(text)

                                            if plaintiff_match_1:
                                                plaintiff_name_1 = plaintiff_match_1.group(1).strip()
                                                blocks = plaintiff_name_1.split('\n\n')
                                                plaintiff_name_1 = blocks[-1]
                                                plaintiff_name = plaintiff_name_1

                                        if not plaintiff_name_2:
                                            plaintiff_pattern_2 = re.compile(r'([\s\S]+?)\n?\n?Plaintiff,', re.MULTILINE)
                                            plaintiff_match_2 = plaintiff_pattern_2.search(text)

                                            if plaintiff_match_2:
                                                plaintiff_name_2 = plaintiff_match_2.group(1).strip()
                                                blocks = plaintiff_name_2.split('\n\n')
                                                plaintiff_name_2 = blocks[-1]
                                                plaintiff_name = plaintiff_name_2
                                                
                                        if defendant_name != '':
                                            record['first_name'] = defendant_name.split()[0]
                                        else:
                                            record['first_name'] = defendant_name
                                        if ' ' in defendant_name:
                                            record['last_name'] = ' '.join(defendant_name.split()[1:])
                                        else:
                                            record['last_name'] = ' '
                                    except Exception as e:  # Correct exception syntax
                                        # Log and print any errors during the process
                                        print(f'Summons extract error: {e}')
                                        logger.error(f'Summons extract error: {e}', exc_info=True)
                        except Exception as e:
                            logger.error(f'credtor: {e}', exc_info=True)
                            print('error credtor '+ str(e))
                    if 'exhibit' in pdf_file.lower() or 'statement of authorization' in pdf_file.lower():
                        record = exhibit_info(pdf_path,record,folder_path,folder,pdf_file.lower())
                                    
                            
                    
                except Exception as e:
                    logger.error(e, exc_info=True)
                    print('error 1'+str(e))
            
            print(record)
            logger.info(f'Record: {record}')
            records.append(record)

            at = airtable.Airtable('appho2OWyOBvn6PPU', 'patwsQ3w8O4VGC05S.01759369d41db17822bcf0074f5daf046cc68ef136677dd68a15550f0e843bef')
            
            plaintiffs = ', '.join([p['name'] for p in case_detail['plaintiffs']])
            def_names, def_entities = parse_entities(case_detail['defendants'])
            
            record['creditor_name'] = plaintiffs
            record['company_sued'] = def_entities

            for defendant in def_names:
                record['first_name'] = defendant.split()[0]
                record['last_name'] = ' '.join(defendant.split()[1:])
                lead = {
                    'Page Link': record['folder'] if 'folder' in record else None,
                    'First Name': record['first_name'] if 'first_name' in record else None,
                    'Last Name': record['last_name'] if 'last_name' in record else None,
                    'phone': record['tel'] if 'tel' in record else None,
                    'email': record['email'] if 'email' in record else None,
                    'CREDITOR NAME': record['creditor_name'] if 'creditor_name' in record else None,
                    'COMPANY SUED': record['company_sued'] if 'company_sued' in record else None,
                    'BALANCE': record["price"] if 'price' in record else 0,
                    'BUSINESS ADDRESS': record['business_address'] if 'business_address' in record else None,
                    'COUNTY': record['county'] if 'county' in record else None,
                    'DATE': record['date'] if 'date' in record else None
                }

                logger.info(f'Prepared record: {record}')
                existing_lead = Lead.objects.filter(folder_id=record['folder'] if 'folder' in record else '').first()

                if existing_lead:
                    # Update the existing lead record
                    existing_lead.first_name = record['first_name'] if 'first_name' in record else ''
                    existing_lead.last_name = record['last_name'] if 'last_name' in record else ''
                    existing_lead.email = record['email'] if 'email' in record else ''
                    existing_lead.phone = record['tel'] if 'tel' in record else ''
                    existing_lead.creditor_name = record['creditor_name'] if 'creditor_name' in record else ''
                    existing_lead.company_suied = record['company_sued'] if 'company_sued' in record else ''
                    existing_lead.price = record['price'] if 'price' in record else ''
                    existing_lead.county = record['county'] if 'county' in record else ''
                    existing_lead.date = record['date'] if 'date' in record else ''
                    existing_lead.business_address = record['business_address'] if 'business_address' in record else ''
                    # ... Update other fields as needed ...
                    existing_lead.save()
                    # at.update('Scrape Leads',existing_lead.airtable_id, {
                    #     'Page Link': record['folder'] if 'folder' in record else None,
                    #     'First Name': record['first_name'] if 'first_name' in record else None,
                    #     'Last Name': record['last_name'] if 'last_name' in record else None,
                    #     'phone': record['tel'] if 'tel' in record else None,
                    #     'email': record['email'] if 'email' in record else None,
                    #     'CREDITOR NAME': record['creditor_name'] if 'creditor_name' in record else None,
                    #     'COMPANY SUED': record['company_sued'] if 'company_sued' in record else None,
                    #     'BALANCE': record["price"] if 'price' in record else 0,
                    #     'BUSINESS ADDRESS': record['business_address'] if 'business_address' in record else None,
                    #     'COUNTY': record['county'] if 'county' in record else None,
                    #     'DATE': record['date'] if 'date' in record else None
                    # })
                else:
                    Lead.objects.create(
                        first_name=record['first_name'] if 'first_name' in record else '',
                        last_name=record['last_name'] if 'last_name' in record else '',
                        email=record['email'] if 'email' in record else '',
                        phone=record['tel'] if 'tel' in record else '',
                        creditor_name=record['creditor_name'] if 'creditor_name' in record else '',
                        company_suied=record['company_sued'] if 'company_sued' in record else '',
                        price=record["price"] if 'price' in record else '',
                        county=record["county"] if 'county' in record else '',
                        date=record["date"] if 'date' in record else '',
                        business_address=record["business_address"] if 'business_address' in record else '',
                        folder_id=record['folder'] if 'folder' in record else '',
                        status='done',
                        airtable_id = id.get('id', None)
                    )
                at.create('Scrape Leads', lead)
            
            
            

def extract_folders(records):  
    folder_path='pdfs'
    output_folder='ocr'
    # Create the output folder if it doesn't exist
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)

    # Get a list of all files in the pdfs folder
    folders = os.listdir(folder_path)
    # folders = [folder for folder in folders if '907737-23' in folder]
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(extract_texts, folder,records) for folder in [folders[4],folders[5],folders[6],folders[7]]]
        for future in futures:
            future.result()  
            
def extract_folder(request, folder):  
    folder_path='pdfs'
    decoded_folder = unquote(folder)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
    decoded_folder = decoded_folder.replace('https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=','').replace('&display=all','')
    records = []
    extract_texts(decoded_folder.replace('/','-'),records)

    context = {'segment': 'index'}
    context['records'] = []
    leads = Lead.objects.all()
    context['records'] = leads
    html_template = loader.get_template('home/index.html')
    return HttpResponse(html_template.render(context, request))

                         
def scrape(request):
    print('starting scrape function')
    try:
        optionsUC = webdriver.ChromeOptions()
        optionsUC.add_argument('--window-size=360,640')
        optionsUC.add_argument('--no-sandbox')
        optionsUC.add_argument('--headless')
        optionsUC.add_argument('start-maximized')
        count_types = ['Kings County Supreme Court', 'Monroe County Supreme Court', 'Washington County Supreme Court', 'Ontario County Supreme Court']
        try:
            current_date = datetime.utcnow()
            one_day = timedelta(days=0)
            previous_date = (current_date - one_day).strftime('%m/%d/%Y')
            driver =  webdriver.Chrome(options=optionsUC)
            context = {'segment': 'index'}
            records = []
            try:
                # return False
                driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})") 
                driver.get('https://iapps.courts.state.ny.us/nyscef/CaseSearch?TAB=courtDateRange')
                sleep(5)
                client = AnticaptchaClient(api_key)
                site_key = driver.find_element(By.CSS_SELECTOR, 'div.g-recaptcha').get_attribute('data-sitekey')  # grab from site
                task = NoCaptchaTaskProxylessTask(url, site_key)
                job = client.createTask(task)
                print("Waiting to solution by Anticaptcha workers")
                job.join()
                # Receive response
                response = job.get_solution_response()
                print("Received solution", response)
                logger.info(f'Anticaptcha response received')
                recaptcha_textarea = driver.find_element(By.ID, "g-recaptcha-response")
                driver.execute_script(f"arguments[0].innerHTML = '{response}';", recaptcha_textarea)
                driver.execute_script("document.getElementById('captcha_form').submit();")
                links = []
                for count_type in count_types:
                    try:
                        driver.get('https://iapps.courts.state.ny.us/nyscef/CaseSearch?TAB=courtDateRange')
                        sleep(5)
                        if(len(driver.find_elements(By.CSS_SELECTOR,'div.g-recaptcha'))>0):
                            client = AnticaptchaClient(api_key)
                            site_key = driver.find_element(By.CSS_SELECTOR, 'div.g-recaptcha').get_attribute('data-sitekey')  # grab from site
                            task = NoCaptchaTaskProxylessTask(url, site_key)
                            job = client.createTask(task)
                            print("Waiting to solution by Anticaptcha workers")
                            job.join()
                            # Receive response
                            response = job.get_solution_response()
                            print("Received solution", response)
                            logger.info(f'Anticaptcha response received')
                            recaptcha_textarea = driver.find_element(By.ID, "g-recaptcha-response")
                            driver.execute_script(f"arguments[0].innerHTML = '{response}';", recaptcha_textarea)
                            driver.execute_script("document.getElementById('captcha_form').submit();")
                        WebDriverWait(driver, 600).until(EC.presence_of_element_located((By.XPATH,f'//*[@id="selCountyCourt"]/option[text()="{count_type}"]')))
                        driver.find_element(By.XPATH,f'//*[@id="selCountyCourt"]/option[text()="{count_type}"]').click()
                        driver.find_element(By.CSS_SELECTOR,'#txtFilingDate').send_keys(Keys.BACKSPACE * 50)
                        driver.find_element(By.CSS_SELECTOR,'#txtFilingDate').send_keys(previous_date)
                        driver.find_element(By.CSS_SELECTOR,'input[type*="submit"]').click()
                        sleep(7)
                        if len(driver.find_elements(By.XPATH,'//*[@id="selSortBy"]/option[text()="Case Type"]'))>0:
                            driver.find_element(By.XPATH,'//*[@id="selSortBy"]/option[text()="Case Type"]').click()
                            driver.find_element(By.CSS_SELECTOR,'caption input[type*="submit"]').click()
                            sleep(6)
                        elements = driver.find_elements(By.CSS_SELECTOR, '#form > table.NewSearchResults > tbody > tr')
                        for i,e in enumerate(elements):
                            if 'Commercial' in driver.find_element(By.CSS_SELECTOR,'td:nth-child(1) > a').get_attribute('innerHTML'):
                                links.append(e.find_element(By.CSS_SELECTOR,'td:nth-child(4)').get_attribute('href'))
                        next_page = True
                        while(next_page):
                            next_page_elemnt = driver.find_elements(By.XPATH,'//*[@class="pageNumbers"]/a[text()=">>"]')
                            if len(next_page_elemnt)>0:
                                driver.get(next_page_elemnt[0].get_attribute('href'))
                                sleep(4)
                                elements = driver.find_elements(By.CSS_SELECTOR, '#form > table.NewSearchResults > tbody > tr > td:nth-child(1) > a')
                                for i,e in enumerate(elements):
                                    links.append(e.get_attribute('href'))
                            else:
                                next_page = False
                                        
                        # driver.close()
                        driver.switch_to.window(driver.window_handles[0])
                    except Exception as e:
                        logger.error(e, exc_info=True)
                        print(e)
                
                # print(links)
                # return False
                driver.quit()
                PROCNAME = "chromedriver" # or chromedriver or IEDriverServer
                for proc in psutil.process_iter():
                    # check whether the process name matches
                    if proc.name() == PROCNAME:
                        proc.kill()
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    futures = [executor.submit(download_pdfs, link, records) for link in links]
                    for future in futures:
                        future.result()
            except Exception as e:
                logger.error(e, exc_info=True)
                print('-'*50)
                print(e)
            finally:
                PROCNAME = "chromedriver" # or chromedriver or IEDriverServer
                for proc in psutil.process_iter():
                    # check whether the process name matches
                    if proc.name() == PROCNAME:
                        proc.kill()
                context['records'] = records
                html_template = loader.get_template('home/index.html')
                logger.info('Returning HTML template')
                return HttpResponse(html_template.render(context, request))
        except Exception as e:
            logger.error(f'Passed error: {e}', exc_info=True)
            print('error2'+str(e))
            pass
        sleep(20)
    except Exception as e:
        logger.error(e, exc_info=True)
        print('error3'+str(e))
        pass
    
    
def getRecordsFromPhoneBurner(request):
    return HttpResponse('ok')

def convertImg(image):
    img_bytes = io.BytesIO()
    image.save(img_bytes, format='JPEG')  # Save as JPEG (or other supported format)
    img_bytes = img_bytes.getvalue()
    
    # # Decode the image bytes using OpenCV
    return cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)

def getBylocation(image):
    # Decode the image bytes using OpenCV
    img = convertImg(image)
    # choose_location(image)
    height, width, channel = img.shape
    # Resizing image if required
    img = cv2.resize(img, (width//2, height//2))

    # Convert image to grey scale
    gray_image = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # Converting grey image to binary image by Thresholding
    threshold_img = cv2.threshold(gray_image, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    # roi_coordinate = [[(9, 84), (486, 119)]]
    roi_coordinate = [[(100, 174), (375, 198)]]
    top_left_x = list(roi_coordinate[0])[0][0]
    bottom_right_x = list(roi_coordinate[0])[1][0]
    top_left_y = list(roi_coordinate[0])[0][1]
    bottom_right_y = list(roi_coordinate[0])[1][1]
    img_cropped = threshold_img[top_left_y:bottom_right_y, top_left_x:bottom_right_x]
    # Draw rectangle for area of interest (ROI)
    cv2.rectangle(img, (top_left_x, top_left_y), (bottom_right_x, bottom_right_y), (0, 255, 0), 3)

    # OCR section to extract text using pytesseract in python
    # configuring parameters for tesseract
    custom_config = r'--oem 3 --psm 6'

    # Extract text within specific coordinate using pytesseract OCR Python
    # Providing cropped image as input to the OCR
    creditor_name = pytesseract.image_to_string(img_cropped, config=custom_config, lang='eng')
    creditor_name= creditor_name.replace(',','')
    font = cv2.FONT_HERSHEY_DUPLEX
    # Red color code
    red_color = (0,0,255)

    cv2.putText(img, f'{creditor_name}', (top_left_x-25, top_left_y - 10), font, 0.5, red_color, 1)
    # cv2.imshow('img', img)
    # cv2.waitKey(0)
    return creditor_name


def exhibit_info(pdf_path, record, folder_path, folder,pdf_file):
    tel = []
    email = []
    business_address = ''
    business_city = ''
    business_state = ''
    business_zip = ''
    first_name = ''
    last_name = ''
    date = ''
    county = ''
    matches_def = []
    try:
        images = convert_from_path(pdf_path)
        for i, image in enumerate(images):
            if i != -1:
                # Perform OCR on each image
                text = pytesseract.image_to_string(image, lang='eng')
                pattern_def = r'charge of e-filing for(.*?)\(the \“Firm\”\)'
                # identify the defendant
                if not matches_def:
                    regex = re.compile(pattern_def, re.DOTALL)
                    matches_def = regex.findall(' '.join(text.split('\n')))
                    try:
                        record['Defendant'] = matches_def[0].strip()
                    except IndexError:
                        pass
                try:
                    img = convertImg(image)
                    kernel_size = 5
                    blur_gray = cv2.GaussianBlur(img,(kernel_size, kernel_size),0)
                    low_threshold = 50
                    high_threshold = 150
                    edges = cv2.Canny(blur_gray, low_threshold, high_threshold)
                    rho = 1 
                    theta = np.pi / 180 
                    threshold = 15 
                    min_line_length = 250
                    max_line_gap = 5
                    line_image = np.copy(img) * 0
                    lines = cv2.HoughLinesP(edges, rho, theta, threshold, np.array([]),
                                        min_line_length, max_line_gap)
                    for line in lines:
                        try:
                            for x1,y1,x2,y2 in line:
                                cv2.line(line_image,(x1,y1),(x2,y2),(255,0,0),5)
                        except Exception as e:
                            print('creditor_name2 error'+str(e))
                    img = cv2.addWeighted(img, 1, line_image, 1, 0)
                    
                    # threshold the grayscale image
                    thresh = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

                    # use morphology erode to blur horizontally
                    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (151, 3))
                    morph = cv2.morphologyEx(thresh, cv2.MORPH_DILATE, kernel)

                    # find contours
                    cntrs = cv2.findContours(morph, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    cntrs = cntrs[0] if len(cntrs) == 2 else cntrs[1]

                    # find the topmost box
                    ythresh = 1000000
                    for c in cntrs:
                        box = cv2.boundingRect(c)
                        x,y,w,h = box
                        if y < ythresh:
                            topbox = box
                            ythresh = y

                    # Draw contours excluding the topmost box
                    result = img.copy()
                    x_index = 1
                    for c in cntrs:
                        box = cv2.boundingRect(c)
                        if box != topbox:
                            x,y,w,h = box
                            text_region = thresh[y:y + h, x:x + w]
                            # enhanced_region = cv2.bitwise_not(text_region)
                            custom_config = r'--oem 3 --psm 6'
                            extracted_text = pytesseract.image_to_string(text_region, config=custom_config).strip()
                            # cv2.imshow("GRAY", text_region)
                            # cv2.waitKey(0)
                            if ('phone' in extracted_text.lower() or '| ' in extracted_text) and len(re.findall('Cell Phone:[\s\S]*[0-9]{9,10}|[0-9]{2,3}-[0-9]{2,3}-[0-9]{2,5}|\([0-9]{2,3}\) [0-9]{2,3}-[0-9]{2,5}|\([0-9]{2,3}\)[0-9]{2,3}-[0-9]{2,5}',extracted_text)) > 0:
                                try:
                                    tel.append(re.findall('Cell Phone:[\s\S]*[0-9]{9,10}|[0-9]{2,3}-[0-9]{2,3}-[0-9]{2,5}|\([0-9]{2,3}\) [0-9]{2,3}-[0-9]{2,5}|\([0-9]{2,3}\)[0-9]{2,3}-[0-9]{2,5}',extracted_text)[0])
                                    cv2.rectangle(result, (x, y), (x+w, y+h), (0, 0, 255), 2)
                                except Exception as e:
                                    print('phone error'+str(e))

                            
                            if len(re.findall('[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.com|[a-zA-Z0-9._%+ -]*@[a-zA-Z0-9.-]+ com|[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.net|[a-zA-Z0-9._%+ -]*@[a-zA-Z0-9.-]+ net|[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z0-9]{1,4}',extracted_text)) > 0:
                                try:
                                    foundEmail = re.findall('[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.com|[a-zA-Z0-9._%+ -]*@[a-zA-Z0-9.-]+ com|[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.net|[a-zA-Z0-9._%+ -]*@[a-zA-Z0-9.-]+ net|[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z0-9]{1,4}',extracted_text)
                                    for email_addr in foundEmail:
                                        if 'accounting'not in email_addr and 'admin'not in email_addr and 'customer'not in email_addr:
                                            email.append(email_addr.strip())
                                            cv2.rectangle(result, (x, y), (x+w, y+h), (0, 0, 255), 2)
                                except Exception as e:
                                    logger.error(f'Email error: {e}', exc_info=True)
                                    print('email error'+str(e))
                                
                            if len(re.findall('Full Name:.*|Full Name;.*|Contact Name:.*|Contact Name;.*|Owner: Name:.*',extracted_text))> 0 and first_name == '' and last_name == '':
                                try:
                                    text_region = thresh[y-35:y + h, x:x + w]
                                    croped_text = pytesseract.image_to_string(text_region, config=custom_config).strip()
                                    cv2.rectangle(result, (x, y-35), (x+w, y+h), (0, 0, 255), 2)
                                    print(croped_text)
                                    if len(re.findall('Full Name:.*|Full Name;.*|Contact Name:.*|Contact Name;.*|Owner: Name:.*',croped_text))> 0:
                                        name = re.findall('Full Name:.*|Full Name;.*|Contact Name:.*|Contact Name;.*|Owner: Name:.*',croped_text)[0].replace('Full Name:','').replace('Full Name;','').replace('Contact Name:','').replace('Contact Name;','').replace('Owner: Name:','').strip()
                                        print(extracted_text)
                                        logger.info(extracted_text)
                                        first_name = name.split(' ')[0] if len(name.split(' ')) > 0 else ''
                                        last_name = (name.split(' ')[1] if len(name.split(' ')) > 1 else '')+(name.split(' ')[2] if len(name.split(' ')) > 2 else '')
                                        record['first_name'] = first_name
                                        record['last_name'] = last_name
                                except Exception as e:
                                    logger.error(f'Full name error: {e}', exc_info=True)
                                    print('full name error'+str(e))
                                    
                            if len(re.findall('First Name:.*',extracted_text)) > 0 and first_name == '':
                                try:
                                    first_name = re.findall('First Name:.*',extracted_text)[0].replace('First Name:','')
                                    record['first_name'] = first_name
                                    cv2.rectangle(result, (x, y), (x+w, y+h), (0, 0, 255), 2)
                                except Exception as e:
                                    logger.error(f'First name error: {e}', exc_info=True)
                                    print('first name error'+str(e))
                            
                            if len(re.findall('Last Name:.*',extracted_text)) > 0 and last_name == '':
                                try:
                                    last_name = re.findall('Last Name:.*',extracted_text)[0].replace('Last Name:', '')
                                    record['last_name'] = last_name
                                    cv2.rectangle(result, (x, y), (x+w, y+h), (0, 0, 255), 2)
                                except Exception as e:
                                    logger.error(f'Last name error: {e}', exc_info=True)
                                    print('last name error'+str(e))
                                
                                
                            if 'OWNER/GUARANTOR #1' in extracted_text and first_name == '' and last_name == '':
                                try:
                                    text_region = thresh[y+15:y + h + 140, x:x + w]
                                    croped_text = pytesseract.image_to_string(text_region, config=custom_config).strip()
                                    cv2.rectangle(result, (x, y), (x+w, y+h+140), (0, 0, 255), 2)
                                    if len(re.findall('Name:.*|Name;.*|By:.*|By;.*',croped_text))> 0:
                                        name = re.findall('Name:.*|Name;.*|By:.*|By;.*',croped_text)[0].replace('Name:','').replace('Name;','').replace('By:','').replace('By;','').strip()
                                        first_name = name.split(' ')[0] if len(name.split(' ')) > 0 else ''
                                        last_name = (name.split(' ')[1] if len(name.split(' ')) > 1 else '')+(name.split(' ')[2] if len(name.split(' ')) > 2 else '')
                                        record['first_name'] = first_name
                                        record['last_name'] = last_name
                                except Exception as e:
                                    logger.error(f'OWNER/GUARANTOR error: {e}', exc_info=True)
                                    print('OWNER/GUARANTOR #1 error'+str(e))
                            if 'GUARANTOR\'S INFORMATION' in extracted_text and first_name == '' and last_name == '':
                                try:
                                    text_region = thresh[y+15:y + h + 140, x:x + w]
                                    croped_text = pytesseract.image_to_string(text_region, config=custom_config).strip()
                                    cv2.rectangle(result, (x, y), (x+w, y+h+140), (0, 0, 255), 2)
                                    if len(re.findall('| ',croped_text))> 0:
                                        name = re.findall('| ',croped_text)[0].replace('| ','').strip()
                                        first_name = name.split(' ')[0] if len(name.split(' ')) > 0 else ''
                                        last_name = (name.split(' ')[1] if len(name.split(' ')) > 1 else '')+(name.split(' ')[2] if len(name.split(' ')) > 2 else '')
                                        record['first_name'] = first_name
                                        record['last_name'] = last_name
                                except Exception as e:
                                    logger.error(f'GUARANTOR\'S INFORMATION error: {e}', exc_info=True)
                                    print('GUARANTOR\'S INFORMATION error'+str(e))
                                    
                            if 'OWNER/GUARANTOR (#1)' in extracted_text and first_name == '' and last_name == '':
                                try:
                                    text_region = thresh[y+15:y + h + 140, x:x + w + 200]
                                    croped_text = pytesseract.image_to_string(text_region, config=custom_config).strip()
                                    cv2.rectangle(result, (x, y), (x+w+ 200, y+h+140), (0, 0, 255), 2)
                                    if len(re.findall('Name:.*|Name;.*|By:.*',croped_text))> 0:
                                        name = re.findall('Name:.*|Name;.*|By:.*',croped_text)[0].replace('Name:','').replace('Name;','').replace('By:','').strip()
                                        first_name = name.split(' ')[0] if len(name.split(' ')) > 0 else ''
                                        last_name = (name.split(' ')[1] if len(name.split(' ')) > 1 else '')+(name.split(' ')[2] if len(name.split(' ')) > 2 else '')
                                        record['first_name'] = first_name
                                        record['last_name'] = last_name
                                except Exception as e:
                                    logger.error(f'OWNER/GUARANTOR  error: {e}', exc_info=True)
                                    print('OWNER/GUARANTOR (#1) error'+str(e))
                                    
                            if 'Name of Owner Guarantor 1:' in extracted_text and first_name == '' and last_name == '':
                                try:
                                    text_region = thresh[y:y + h + 10, x:x + w]
                                    croped_text = pytesseract.image_to_string(text_region, config=custom_config).replace('Name of Owner Guarantor 1:','').strip()
                                    cv2.rectangle(result, (x, y), (x+w, y+h+10), (0, 0, 255), 2)
                                    name = croped_text.strip()
                                    first_name = name.split(' ')[0] if len(name.split(' ')) > 0 else ''
                                    last_name = (name.split(' ')[1] if len(name.split(' ')) > 1 else '')+(name.split(' ')[2] if len(name.split(' ')) > 2 else '')
                                    record['first_name'] = first_name
                                    record['last_name'] = last_name
                                except Exception as e:
                                    logger.error(f'Name of Owner Guarantor error: {e}', exc_info=True)
                                    print('Name of Owner Guarantor 1: error'+str(e))
                            

                            if 'Guarantor(s) Name:' in extracted_text and first_name == '' and last_name == '':
                                try:
                                    text_region = thresh[y:y + h + 10, x:x + w]
                                    croped_text = pytesseract.image_to_string(text_region, config=custom_config).replace('Guarantor(s) Name:','').replace('_','').replace('—','').strip()
                                    cv2.rectangle(result, (x, y), (x+w, y+h+10), (0, 0, 255), 2)
                                    name = croped_text.strip()
                                    first_name = name.split(' ')[0] if len(name.split(' ')) > 0 else ''
                                    last_name = (name.split(' ')[1] if len(name.split(' ')) > 1 else '')+(name.split(' ')[2] if len(name.split(' ')) > 2 else '')
                                    record['first_name'] = first_name
                                    record['last_name'] = last_name
                                except Exception as e:
                                    logger.error(f'Guarantor(s) Name error: {e}', exc_info=True)
                                    print('Guarantor(s) Name: error'+str(e))
                                    
                            
                            if 'phone' in extracted_text.lower():
                                try:
                                    text_region = thresh[y-60:y + h, x:x + w+80]
                                    croped_text = pytesseract.image_to_string(text_region, config=custom_config).strip()
                                    cv2.rectangle(result, (x+80, y-60), (x+w, y+h), (0, 0, 255), 2)
                                    if len(re.findall('Cell Phone:[\s\S]*[0-9]{9,10}|[0-9]{2,3}-[0-9]{2,3}-[0-9]{2,5}|\([0-9]{2,3}\) [0-9]{2,3}-[0-9]{2,5}|\([0-9]{2,3}\)[0-9]{2,3}-[0-9]{2,5}',croped_text)) > 0:
                                        tel.append(re.findall('Cell Phone:[\s\S]*[0-9]{9,10}|[0-9]{2,3}-[0-9]{2,3}-[0-9]{2,5}|\([0-9]{2,3}\) [0-9]{2,3}-[0-9]{2,5}|\([0-9]{2,3}\)[0-9]{2,3}-[0-9]{2,5}',croped_text)[0])
                                    
                                except Exception as e:
                                    logger.error(f'Phone error: {e}', exc_info=True)
                                    print('Phone: error'+str(e)) 
                            
                            if 'Print Name' in extracted_text and first_name == '' and last_name == '':
                                try:
                                    text_region = thresh[y-50:y + h, x-60:x + w+40]
                                    croped_text = pytesseract.image_to_string(text_region, config=custom_config).strip()
                                    cv2.rectangle(result, (x-60, y-50), (x+w+40, y+h), (0, 0, 255), 2)
                                    name = croped_text.replace('PrintName','').replace('Print Name','').replace('andTitle','').replace('and Title','').strip().replace('()','').replace('( )','')
                                    first_name = name.split(' ')[0] if len(name.split(' ')) > 0 else ''
                                    last_name = (name.split(' ')[1] if len(name.split(' ')) > 1 else '')+(name.split(' ')[2] if len(name.split(' ')) > 2 else '')
                                    record['first_name'] = first_name
                                    record['last_name'] = last_name
                                except Exception as e:
                                    logger.error(f'Print Name: {e}', exc_info=True)
                                    print('Print Name: error'+str(e))    
                                    
                            if ('Printed Name:' in extracted_text or 'Print Name:' in extracted_text) and first_name == '' and last_name == '':
                                try:
                                    text_region = thresh[y:y + h + 10, x:x + w]
                                    croped_text = pytesseract.image_to_string(text_region, config=custom_config).replace('Printed Name:','').replace('Print Name:','').replace('Signature:','').replace(':','').strip()
                                    cv2.rectangle(result, (x, y), (x+w, y+h+10), (0, 0, 255), 2)
                                    name = croped_text.strip()
                                    first_name = name.split(' ')[0] if len(name.split(' ')) > 0 else ''
                                    last_name = (name.split(' ')[1] if len(name.split(' ')) > 1 else '')+(name.split(' ')[2] if len(name.split(' ')) > 2 else '')
                                    record['first_name'] = first_name
                                    record['last_name'] = last_name
                                except Exception as e:
                                    logger.error(f'Printed Name: {e}', exc_info=True)
                                    print('Printed Name: error'+str(e))
                            
                            if 'Street Address' in extracted_text and business_address == '':
                                try:
                                    text_region = thresh[y-50:y + h, x:x + w+40]
                                    croped_text = pytesseract.image_to_string(text_region, config=custom_config).strip()
                                    cv2.rectangle(result, (x, y-50), (x+w+40, y+h), (0, 0, 255), 2)
                                    business_address = croped_text.replace('Street Address', '').replace('gS','').replace(':','').strip()
                                    record['business_address'] = business_address.replace('SAME AS ABOVE','')
                                    if(len(re.findall('\n',record['business_address']))>0):
                                        record['business_address'] = record['business_address'].split('\n')
                                        record['business_address'] = record['business_address'][1]
                                    print(record['business_address'])
                                    logger.info(record['business_address'])
                                except Exception as e:
                                    logger.error(f'Street Address error: {e}', exc_info=True)
                                    print('Street Address'+str(e))
                                    
                            if 'City, State and Zip Code' in extracted_text and business_zip == '' and business_state == '' and business_city == '':
                                try:
                                    text_region = thresh[y-50:y + h, x:x + w]
                                    croped_text = pytesseract.image_to_string(text_region, config=custom_config).strip()
                                    cv2.rectangle(result, (x, y-50), (x+w, y+h), (0, 0, 255), 2)
                                    text = croped_text.replace('City, State and Zip Code', '').strip()
                                    business_zip = re.findall('\d+',text)[0].replace(',', '').strip() if len(re.findall('\d+',text)) > 0 else ''
                                    business_state = re.findall(',.*?\d+',text)[0].replace(business_zip, '').replace(',', '').strip() if len(re.findall(',.*?\d+',text)) > 0 else ''
                                    business_city = re.findall('.*?,',text)[0].replace(',', '').strip() if len(re.findall('.*?,',text)) > 0 else ''
                                    record['business_city'] = business_city
                                    record['business_state'] = business_state
                                    record['business_zip'] = business_zip
                                except Exception as e:
                                    logger.error(f'City, State and Zip Code error: {e}', exc_info=True)
                                    print('City, State and Zip Code'+str(e))
                                    
                            if len(re.findall('Business Address:.*?:|Business street address:.*|Business Location Street Address:.*?city|Physical Address:.*|Home Address:.*|Address of Executive Offices:.*?City|Address of Executive Offices:.*',extracted_text)) > 0 and (business_address == '' or business_address == ', ,'):
                                try:
                                    business_address = re.findall('Business Address:.*?:|Business street address:.*|Business Location Street Address:.*?city|Physical Address:.*|Home Address:.*|Address of Executive Offices:.*?City|Address of Executive Offices:.*',extracted_text)[0].replace('Address of Executive Offices:','').replace('Business Location Street Address:','').replace('Business Address:','').replace('Business street address:','').replace('City','').replace('city','').replace('Physical Address:','').replace('Home Address:','').replace('Address:.*','')
                                    if(len(re.findall('[a-zA-Z]+?:',business_address))>0):
                                        business_address = business_address.replace(re.findall('[a-zA-Z]+?:',business_address)[0],'')
                                    record['business_address'] = business_address.replace('SAME AS ABOVE','')
                                    cv2.rectangle(result, (x, y), (x+w, y+h), (0, 0, 255), 2)
                                except Exception as e:
                                    logger.error(f'Business address error: {e}', exc_info=True)
                                    print('business address error'+str(e))
                                    
                                    
                            if len(re.findall('City/State:.*',extracted_text)) > 0 and business_city == '' and business_state == '':
                                try:
                                    city_state= re.findall('City/State:.*',extracted_text)[0].split(',')
                                    if len(city_state)>1:
                                        record['business_city'] = city_state[0]
                                        record['business_state'] = city_state[1]
                                    cv2.rectangle(result, (x, y), (x+w, y+h), (0, 0, 255), 2)
                                except Exception as e:
                                    logger.error(f'city/state error: {e}', exc_info=True)
                                    print('city/state error'+str(e)) 
                                    
                                    
                            if len(re.findall('City:.*?:|Business city:.*?:|Business Location Street Address:.*?Stata|City:.*|city:.*',extracted_text)) > 0 and business_city == '':
                                try:
                                    business_city = re.findall('City:.*?:|Business city:.*?:|Business Location Street Address:.*?Stata|City:.*|city:.*',extracted_text)[0].replace('City:','').replace('Business city:','').replace('—', '').replace('city','').replace('Business Location Street Address:','').replace('Stata','').replace(business_address,'')
                                    if(len(re.findall('[a-zA-Z]+?:',business_city))>0):
                                        business_city = business_city.replace(re.findall('[a-zA-Z]+?:',business_city)[0],'')
                                    record['business_city'] = business_city
                                    cv2.rectangle(result, (x, y), (x+w, y+h), (0, 0, 255), 2)
                                except Exception as e:
                                    print('city error'+str(e))
                                    
                            if len(re.findall('State:.*?:|State:.*?;|Business state:.*?:|State:.*',extracted_text)) > 0 and business_state == '':
                                try:
                                    business_state = re.findall('State:.*?:|State:.*?;|Business state:.*?:|State:.*',extracted_text)[0].replace(';','').replace('State:','').replace('Business state:','').replace('_', '').replace('—', '').strip()
                                    if(len(re.findall('[a-zA-Z]+?:',business_state))>0):
                                        business_state = business_state.replace(re.findall('[a-zA-Z]+?:',business_state)[0],'')
                                    record['business_state'] = business_state
                                    cv2.rectangle(result, (x, y), (x+w, y+h), (0, 0, 255), 2)
                                except Exception as e:
                                    logger.error(f'business state error: {e}', exc_info=True)
                                    print('business state error'+str(e))
                                    
                            if len(re.findall('Zip:.*?:|Business zip:.*|Zip:.*',extracted_text)) > 0 and business_zip == '':
                                try:
                                    business_zip = re.findall('Zip:.*?:|Business zip:.*|Zip:.*',extracted_text)[0].replace('Zip:','').replace('Business zip:','').replace('—', '')
                                    if(len(re.findall('[a-zA-Z]+?:',business_zip))>0):
                                        business_zip = business_zip.replace(re.findall('[a-zA-Z]+?:',business_zip)[0],'')
                                    record['business_zip'] = business_zip
                                    cv2.rectangle(result, (x, y), (x+w, y+h), (0, 0, 255), 2)
                                except Exception as e:
                                    logger.error(f'business zip error: {e}', exc_info=True)
                                    print('business zip error'+str(e))
                                    
                            if len(re.findall('[0-9]{2}/[0-9]{2}/[0-9]{4}',extracted_text)) > 0 and date == '' and ('NYSCEF' in extracted_text or 'CLERK' in extracted_text) and 'date' not in record:
                                try:
                                    date = re.findall('[0-9]{2}/[0-9]{2}/[0-9]{4}',extracted_text)[0]
                                    print(extracted_text)
                                    logger.info(extracted_text)
                                    record['date'] = date
                                    cv2.rectangle(result, (x, y), (x+w, y+h), (0, 0, 255), 2)
                                except Exception as e:
                                    logger.error(f'date error: {e}', exc_info=True)
                                    print('date error'+str(e))
                                    
                            if(len(re.findall('FILED:[\s\S]*?COUNTY',extracted_text))>0) and county == '':
                                try:
                                    county = re.findall('FILED:[\s\S]*?COUNTY',extracted_text)[0].replace('FILED:','')
                                    record['county'] = county
                                except Exception as e:
                                    logger.error(f'business zip2 error: {e}', exc_info=True)
                                    print('business zip2 error'+str(e))
                                
                                
     
                    cv2.imwrite(folder_path + "/" + folder + '/'  +pdf_file+str(i)+".jpg", result)            
                    
                except Exception as e:
                    logger.error(f'error in text_info: {e}', exc_info=True)
                    print('error in text_info'+str(e))
                    
                    
                if(len(re.findall(' [a-zA-Z]+?,.*?\d*\\nSignature City, State and Zip Code',text))>0) and business_zip == '':
                    try:
                        business_zip = re.findall(' [a-zA-Z]+?,.*?\d*\\nSignature City, State and Zip Code',text)[0].replace('Signature City, State and Zip Code','')
                        if(len(re.findall('\d+',business_zip))>0):
                            business_zip = re.findall('\d+',business_zip)[0]
                        record['business_zip'] = business_zip
                    except Exception as e:
                        logger.error(f'business zip2 error: {e}', exc_info=True)
                        print('business zip2 error'+str(e))
                        
                    
                if(len(re.findall(' [a-zA-Z]+?,.*?\d*\\nSignature City, State and Zip Code',text))>0) and business_city == '':
                    try:
                        business_city = re.findall(' [a-zA-Z]+?,.*?\d*\\nSignature City, State and Zip Code',text)[0].replace('Signature City, State and Zip Code','')
                        if(len(re.findall(',.*\d*',business_city))>0):
                            business_city = business_city.replace(re.findall(',.*\d*',business_city)[0],'')
                        record['business_city'] = business_city
                    except Exception as e:
                        logger.error(f'business city2 error: {e}', exc_info=True)
                        print('business city2 error'+str(e))
                    
                if(len(re.findall(' [a-zA-Z]+?,.*?\d*\\nSignature City, State and Zip Code',text))>0) and business_state == '':
                    try:
                        business_state = re.findall(' [a-zA-Z]+?,.*?\d*\\nSignature City, State and Zip Code',text)[0].replace('Signature City, State and Zip Code','')
                        if(len(re.findall('.*,',business_state))>0):
                            business_state = business_state.replace(re.findall('.*,',business_state)[0],'')
                        if(len(re.findall('\d+',business_state))>0):
                            business_state = business_state.replace(re.findall('\d+',business_state)[0],'')
                        record['business_state'] = business_state
                    except Exception as e:
                        logger.error(f'business state2 error: {e}', exc_info=True)
                        print('business state2 error'+str(e))
    except Exception as e:
        logger.error(f'error in text: {e}', exc_info=True)
        print('error in text'+str(e))
        
    record['business_address'] = business_address
    if(',' not in business_address and business_address != ''):
        record['business_address'] = business_address + (', ' + business_city if business_city else '') + (', ' + business_state if business_state else '') + ' '+ business_zip  
    
    print('========================================================================')
    print('========================================================================')
    if('business_address' in record and len(re.findall('\n',record['business_address']))>0):
        record['business_address'] = record['business_address'].split('\n')
        record['business_address'] = record['business_address'][1]
    logger.info(record['business_address'])
    print(record['business_address'])
    print('========================================================================')
    print('========================================================================')
    for t in tel:
        if 'Cell Phone' in t:
            record['tel'] = t.replace('Cell Phone','').replace(':','')  
    if 'tel' not in record and len(tel)>0:
        record['tel'] = tel[0]
        
    try:
        print(os.getcwd().replace('scraping','')+'/' + pdf_path)
        logger.info(f"{os.getcwd().replace('scraping','')}/{pdf_path}")
        doc = fitz.open(os.getcwd().replace('scraping','')+'/' + pdf_path)
        page = doc.load_page(0)
        page_text = page.get_text("text")
        [email.append(word) if 'accounting' not in word and 'admin' not in word and 'customer' not in word else '' for word in page_text.split() if len(re.findall('[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.com|[a-zA-Z0-9._%+ -]*@[a-zA-Z0-9.-]+ com|[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.net|[a-zA-Z0-9._%+ -]*@[a-zA-Z0-9.-]+ net',word))>0]
        doc.close()
    except Exception as e:
        logger.error(f'email pdf error: {e}', exc_info=True)
        print('email pdf error'+str(e))
        
    print(email)
    logger.info(email)
    
    if 'email' not in record and len(email)>0:
        record['email'] = ', '.join(email)
    else:
        record['email'] = ''
        
        
        
def run_cron(request):
    logger.debug("Debug message")
    logger.info("Info message")
    logger.warning("Warning message")
    logger.error("Error message")
            
                    
def scrape_cron():
    print('starting scrape cron')
    cron_job = CronJobs(status="started", log='run started')
    cron_job.save()
    try:
        optionsUC = webdriver.ChromeOptions()
        optionsUC.add_argument('--window-size=360,640')
        optionsUC.add_argument('--no-sandbox')
        # optionsUC.add_argument('--disable-dev-shm-usage')
        optionsUC.add_argument('--headless')
        optionsUC.add_argument('start-maximized')
        count_types = [
            'Kings County Supreme Court',
            'Monroe County Supreme Court',
            'Nassau County Supreme Court',
            'Ontario County Supreme Court',
            'Queens County Supreme Court',
            'Warren County Supreme Court',
            'Tompkins County Supreme Court',
            'Washington County Supreme Court',
            'New York County Supreme Court',
            'Suffolk County Supreme Court'
        ]
        try:
            current_date = datetime.now(tz=pytz.timezone('US/Eastern'))
            one_day = timedelta(days=1)
            previous_date = (current_date - one_day).strftime('%m/%d/%Y')
            print('Getting driver')
            driver =  webdriver.Chrome(options=optionsUC)
            print('Driver success')
            context = {'segment': 'index'}
            records = []
            try:
                # return False
                driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})") 
                driver.get('https://iapps.courts.state.ny.us/nyscef/CaseSearch?TAB=courtDateRange')
                sleep(5)
                client = AnticaptchaClient(api_key)
                try:
                    site_key = driver.find_element(By.CSS_SELECTOR, 'div.g-recaptcha').get_attribute('data-sitekey')  # grab from site
                except NoSuchElementException as e:
                    logger.info(f'Found no captcha element, skipping')
                    site_key = None
                
                if site_key:
                    task = NoCaptchaTaskProxylessTask(url, site_key)
                    job = client.createTask(task)
                    print("Waiting to solution by Anticaptcha workers")
                    job.join()
                    # Receive response
                    response = job.get_solution_response()
                    print("Received solution")
                    logger.info(f'Anticaptcha response received')
                    recaptcha_textarea = driver.find_element(By.ID, "g-recaptcha-response")
                    driver.execute_script(f"arguments[0].innerHTML = '{response}';", recaptcha_textarea)
                    driver.execute_script("document.getElementById('captcha_form').submit();")
                links = []
                for count_type in count_types:
                    logger.info(f'Processing {count_type}')
                    try:
                        driver.get('https://iapps.courts.state.ny.us/nyscef/CaseSearch?TAB=courtDateRange')
                        sleep(5)
                        try:
                            captcha = driver.find_elements(By.CSS_SELECTOR,'div.g-recaptcha')
                        except NoSuchElementException as e:
                            logger.info(f'Found no captcha element, skipping')
                            captcha = []
                        if(len(captcha)>0):
                            client = AnticaptchaClient(api_key)
                            site_key = driver.find_element(By.CSS_SELECTOR, 'div.g-recaptcha').get_attribute('data-sitekey')  # grab from site
                            task = NoCaptchaTaskProxylessTask(url, site_key)
                            job = client.createTask(task)
                            print("Waiting to solution by Anticaptcha workers")
                            job.join()
                            # Receive response
                            response = job.get_solution_response()
                            print("Received solution")
                            logger.info(f'Anticaptcha response received')
                            recaptcha_textarea = driver.find_element(By.ID, "g-recaptcha-response")
                            driver.execute_script(f"arguments[0].innerHTML = '{response}';", recaptcha_textarea)
                            driver.execute_script("document.getElementById('captcha_form').submit();")
                        WebDriverWait(driver, 600).until(EC.presence_of_element_located((By.XPATH,f'//*[@id="selCountyCourt"]/option[text()="{count_type}"]')))
                        driver.find_element(By.XPATH,f'//*[@id="selCountyCourt"]/option[text()="{count_type}"]').click()
                        driver.find_element(By.CSS_SELECTOR,'#txtFilingDate').send_keys(Keys.BACKSPACE * 50)
                        driver.find_element(By.CSS_SELECTOR,'#txtFilingDate').send_keys(current_date.strftime('%m/%d/%Y'))
                        driver.find_element(By.XPATH,'//button[text()="Search"]').click()
                        sleep(7)

                        solve_capcha(driver, client)

                        if len(driver.find_elements(By.XPATH,'//*[@id="selSortBy"]/option[text()="Case Type"]'))>0:
                            driver.find_element(By.XPATH,'//*[@id="selSortBy"]/option[text()="Case Type"]').click()
                            driver.find_element(By.CSS_SELECTOR,'caption input[type*="submit"]').click()
                            sleep(6)
                        elements = driver.find_elements(By.CSS_SELECTOR, '#form > table.NewSearchResults > tbody > tr')
                        for _ in range(10):
                            elements = driver.find_elements(By.CSS_SELECTOR,
                                                            '#form > table.NewSearchResults > tbody > tr')
                            if elements:
                                break
                            else:
                                logger.info(f'Not finding case type for {driver.current_url}')
                                sleep(1)
                        for i,e in enumerate(elements):
                            if 'Commercial' in e.text:
                                links.append(e.find_element(By.TAG_NAME,'a').get_attribute('href'))
                                        
                        driver.switch_to.window(driver.window_handles[0])
                    except Exception as e:
                        logger.error(e, exc_info=True)
                        print(e)
                
                driver.quit()
                logger.info(f'Submitting {len(links)} to threadpool')
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    futures = [executor.submit(download_pdfs, link, records) for link in links]
                    for future in futures:
                        result = future.result()
                        logger.info(result)
            except Exception as e:
                logger.error(e, exc_info=True)
                print('-'*50)
                print(e)
            finally:
                # driver.quit()
                PROCNAME = "chromedriver" # or chromedriver or IEDriverServer
                for proc in psutil.process_iter():
                    # check whether the process name matches
                    if proc.name() == PROCNAME:
                        proc.kill()
                           
        except Exception as e:
            logger.error(e, exc_info=True)
            print('error2'+str(e))
            pass
        finally:
            # driver.quit()
            PROCNAME = "chromedriver" # or chromedriver or IEDriverServer
            for proc in psutil.process_iter():
                # check whether the process name matches
                if proc.name() == PROCNAME:
                    proc.kill()
            PROCNAME = "chrome" # or chromedriver or IEDriverServer
            for proc in psutil.process_iter():
                # check whether the process name matches
                if proc.name() == PROCNAME:
                    proc.kill()
        cron_job.status = "success"
        cron_job.log = 'run success'
        cron_job.save()
        logger.info('Cron job saved')
    except Exception as e:
        logger.error(e, exc_info=True)
        print('error3'+str(e))
        cron_job.status = "error" 
        cron_job.log = str(e)
        cron_job.save()
        pass


def solve_capcha(driver, client):
    try:
        site_key = driver.find_element(By.CSS_SELECTOR, 'div.g-recaptcha').get_attribute('data-sitekey')
    except NoSuchElementException as e:
        return
    
    task = NoCaptchaTaskProxylessTask(url, site_key)
    job = client.createTask(task)
    print("Waiting to solution by Anticaptcha workers")
    logger.info(f'Found Captcha challenge, solving')
    job.join()
    # Receive response
    response = job.get_solution_response()
    print("Received solution")
    logger.info(f'Anticaptcha response received')
    recaptcha_textarea = driver.find_element(By.ID, "g-recaptcha-response")
    driver.execute_script(f"arguments[0].innerHTML = '{response}';", recaptcha_textarea)
    driver.execute_script("document.getElementById('captcha_form').submit();")


if __name__ == '__main__':
    links = [
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=c32rzkZilC6j3hz1MvvIig==&display=all&courtType=Monroe%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=OjByZpIC50x8saqLb6hUrA==&display=all&courtType=Monroe%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=838RVb88a6vo9PHi0rsx/w==&display=all&courtType=Monroe%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=DLaFsUW4LRuGPzXTfHrMow==&display=all&courtType=Monroe%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=My9JygLcQzK2Ueemj/GpyA==&display=all&courtType=Monroe%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=dNqLy0GXkLof/7OBtMyhCw==&display=all&courtType=Monroe%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=ZgIKrM9KXyjq2LF8XyLlFQ==&display=all&courtType=Monroe%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=cJyFPe2QaixGcCd_PLUS_acicrA==&display=all&courtType=Monroe%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=G6dUbYngsprd5YBIo8bfyQ==&display=all&courtType=Nassau%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=olEa8WEDPTOWEKG1VFi/rQ==&display=all&courtType=Nassau%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=i/vzbvh47ht/5dksAOgckg==&display=all&courtType=Queens%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=ZSHNo_PLUS_5hO48El9HJw6A62Q==&display=all&courtType=Warren%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=QciIkT8iUjf4W8s4712wUg==&display=all&courtType=New%20York%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=TRAVQUD71lWDYV5gb443Qg==&display=all&courtType=New%20York%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=Dro4FcIWLHjVDqabG/4BPw==&display=all&courtType=New%20York%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=KCCsS2Zk6KCBn2UegggivA==&display=all&courtType=New%20York%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=ywZpn6YYRXtfhy1jvsoBoA==&display=all&courtType=New%20York%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=nOmkO3Wm/Q4TPvW_PLUS_RdDc/w==&display=all&courtType=New%20York%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=Vl5W1AExKFEeIq1HzEE3FA==&display=all&courtType=New%20York%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=yEHc_PLUS_JXh14GVRRjILvO50w==&display=all&courtType=New%20York%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=a2KHQV7DPoaHcTYsVLkLcg==&display=all&courtType=New%20York%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=qcMYVSYHwPSM5ZjaHHfGtA==&display=all&courtType=New%20York%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=2BYj326wiY5UFZlQIZTXcw==&display=all&courtType=New%20York%20County%20Supreme%20Court&resultsPageNum=1",
    "https://iapps.courts.state.ny.us/nyscef/DocumentList?docketId=h7JIsilomTOho/1WQt8qnQ==&display=all&courtType=New%20York%20County%20Supreme%20Court&resultsPageNum=1"
    ]
    for link in links:
        download_pdfs(link, [])
    # scrape_cron()