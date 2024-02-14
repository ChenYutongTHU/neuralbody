import numpy as np
from lib.config import cfg
from skimage.measure import compare_ssim
import os
import cv2
from termcolor import colored
from third_parties.lpips import LPIPS
import torch

def set_requires_grad(nets, requires_grad=False):
    if not isinstance(nets, list):
        nets = [nets]
    for net in nets:
        if net is not None:
            for param in net.parameters():
                param.requires_grad = requires_grad
def scale_for_lpips(image_tensor):
    return image_tensor * 2. - 1.

class LpipsComputer(object):
    def __init__(self):
        self.lpips = LPIPS(net='vgg', lpips=True, layers=[0,1,2,3,4])
        set_requires_grad(self.lpips, requires_grad=False)
        self.lpips = self.lpips.cuda() #nn.DataParallel(self.lpips).cuda()
        return
    def compute_lpips(self, pred, target):
        if pred.dim()==3:
            pred, target = pred[None,...], target[None,...]
        with torch.no_grad():
            lpips_loss = self.lpips(scale_for_lpips(pred.permute(0, 3, 1, 2)), 
                                    scale_for_lpips(target.permute(0, 3, 1, 2)))
        return torch.mean(lpips_loss)

class Evaluator:
    def __init__(self):
        self.mse = []
        self.psnr = []
        self.ssim = []
        self.lpips = []
        self.lpips_computer = LpipsComputer()

    def lpips_metric(self, img_pred, img_gt):
        img_pred = torch.tensor(img_pred).float().cuda()
        img_gt = torch.tensor(img_gt).float().cuda()
        lpips = self.lpips_computer.compute_lpips(img_pred, img_gt)
        return lpips.item()

    def psnr_metric(self, img_pred, img_gt):
        mse = np.mean((img_pred - img_gt)**2)
        psnr = -10 * np.log(mse) / np.log(10)
        return psnr

    def ssim_metric(self, img_pred, img_gt, batch):
        if not cfg.eval_whole_img:
            mask_at_box = batch['mask_at_box'][0].detach().cpu().numpy()
            H, W = int(cfg.H * cfg.ratio), int(cfg.W * cfg.ratio)
            mask_at_box = mask_at_box.reshape(H, W)
            # crop the object region
            x, y, w, h = cv2.boundingRect(mask_at_box.astype(np.uint8))
            img_pred = img_pred[y:y + h, x:x + w]
            img_gt = img_gt[y:y + h, x:x + w]

        result_dir = os.path.join(cfg.result_dir, 'comparison')
        os.system('mkdir -p {}'.format(result_dir))
        frame_index = batch['frame_index'].item()
        view_index = batch['cam_ind'].item()
        cv2.imwrite(
            '{}/frame{:04d}_view{:04d}.png'.format(result_dir, frame_index,
                                                   view_index),
            (img_pred[..., [2, 1, 0]] * 255))
        cv2.imwrite(
            '{}/frame{:04d}_view{:04d}_gt.png'.format(result_dir, frame_index,
                                                      view_index),
            (img_gt[..., [2, 1, 0]] * 255))

        # compute the ssim
        ssim = compare_ssim(img_pred, img_gt, multichannel=True)
        return ssim

    def evaluate(self, output, batch):
        rgb_pred = output['rgb_map'][0].detach().cpu().numpy()
        rgb_gt = batch['rgb'][0].detach().cpu().numpy()



        mask_at_box = batch['mask_at_box'][0].detach().cpu().numpy()
        H, W = int(cfg.H * cfg.ratio), int(cfg.W * cfg.ratio)
        mask_at_box = mask_at_box.reshape(H, W)
        # convert the pixels into an image
        white_bkgd = int(cfg.white_bkgd)
        img_pred = np.zeros((H, W, 3)) + white_bkgd
        img_pred[mask_at_box] = rgb_pred
        img_gt = np.zeros((H, W, 3)) + white_bkgd
        img_gt[mask_at_box] = rgb_gt

        if cfg.eval_whole_img:
            rgb_pred = img_pred
            rgb_gt = img_gt

        mse = np.mean((rgb_pred - rgb_gt)**2)
        self.mse.append(mse)

        psnr = self.psnr_metric(rgb_pred, rgb_gt)
        self.psnr.append(psnr)

        rgb_pred = img_pred
        rgb_gt = img_gt
        ssim = self.ssim_metric(rgb_pred, rgb_gt, batch)
        self.ssim.append(ssim)
        
        lpips = self.lpips_metric(img_pred, img_gt)
        self.lpips.append(lpips)


    def summarize(self):
        result_dir = cfg.result_dir
        print(
            colored('the results are saved at {}'.format(result_dir),
                    'yellow'))

        if cfg.test_novel_pose == True:
            result_path = os.path.join(cfg.result_dir, 'metrics_novelpose.pkl')
        else:
            result_path = os.path.join(cfg.result_dir, 'metrics_novelview.pkl')
        os.system('mkdir -p {}'.format(os.path.dirname(result_path)))
        metrics = {'mse': self.mse, 'psnr': self.psnr, 'ssim': self.ssim, 'lpips':self.lpips}
        #np.save(result_path, metrics)
        import pickle
        with open(result_path,'wb') as f:
            pickle.dump(metrics, f)
        print('mse: {}'.format(np.mean(self.mse)))
        print('psnr: {}'.format(np.mean(self.psnr)))
        print('ssim: {}'.format(np.mean(self.ssim)))
        print('lpips: {}'.format(np.mean(self.lpips)))
        self.mse = []
        self.psnr = []
        self.ssim = []
        self.lpips = []
